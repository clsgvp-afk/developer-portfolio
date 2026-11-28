<?php
declare(strict_types=1);

function reply(int $status, array $data): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
    exit;
}

function query(PDO $db, string $sql, array $values = []): PDOStatement
{
    $statement = $db->prepare($sql);
    $statement->execute($values);
    return $statement;
}

function normalizeMovement(array $row): array
{
    $row['created_at'] = (new DateTimeImmutable($row['created_at']))->format(DATE_ATOM);
    return $row;
}

function movement(PDO $db, int $id): never
{
    $raw = file_get_contents('php://input', false, null, 0, 8193);
    try {
        $change = strlen($raw) <= 8192 ? json_decode($raw, true, 8, JSON_THROW_ON_ERROR) : null;
    } catch (JsonException) {
        $change = null;
    }
    $delta = $change['delta'] ?? null;
    $note = $change['note'] ?? '';
    $key = $change['request_id'] ?? null;
    if (!is_int($delta) || $delta === 0 || $delta < -1000000 || $delta > 1000000
        || !is_string($note) || preg_match('/\A[^\x00-\x1F\x7F]{0,160}\z/u', $note) !== 1
        || !is_string($key) || preg_match('/\A[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\z/i', $key) !== 1) {
        reply(422, ['error' => 'invalid_movement']);
    }

    $db->beginTransaction();
    // Serialize retries for the same request before taking the product row lock.
    query($db, 'SELECT pg_advisory_xact_lock(hashtextextended(lower(?), 0))', [$key]);
    $prior = query($db, 'SELECT * FROM movements WHERE request_id = ?', [$key])->fetch();
    if ($prior) {
        $db->commit();
        if ((int)$prior['product_id'] !== $id || (int)$prior['delta'] !== $delta || $prior['note'] !== $note) {
            reply(409, ['error' => 'request_id_conflict']);
        }
        reply(200, normalizeMovement($prior));
    }

    $product = query($db, 'SELECT stock FROM products WHERE id = ? FOR UPDATE', [$id])->fetch();
    if (!$product) {
        $db->rollBack();
        reply(404, ['error' => 'product_not_found']);
    }
    $stock = (int)$product['stock'];
    $after = $stock + $delta;
    if ($after < 0 || $after > 1000000000) {
        $db->rollBack();
        reply(409, ['error' => $after < 0 ? 'insufficient_stock' : 'stock_limit', 'stock' => $stock]);
    }
    query($db, 'UPDATE products SET stock = ? WHERE id = ?', [$after, $id]);
    $saved = query($db, 'INSERT INTO movements (request_id,product_id,delta,stock_after,note)
                         VALUES (?,?,?,?,?) RETURNING *', [$key, $id, $delta, $after, $note])->fetch();
    $db->commit();
    reply(201, normalizeMovement($saved));
}

$method = $_SERVER['REQUEST_METHOD'];
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$db = null;
try {
    $dsn = getenv('DB_DSN');
    $user = getenv('DB_USER');
    if (!$dsn || !$user) {
        throw new RuntimeException('Database configuration missing');
    }
    $db = new PDO($dsn, $user, getenv('DB_PASSWORD') ?: '', [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    if ($method === 'GET' && $path === '/products') {
        reply(200, query($db, 'SELECT * FROM products ORDER BY sku')->fetchAll());
    }
    if (preg_match('~^/products/([1-9][0-9]{0,9})/movements$~', $path, $matches)
        && (int)$matches[1] <= 2147483647) {
        $id = (int)$matches[1];
        if ($method === 'POST') {
            movement($db, $id);
        }
        if ($method === 'GET') {
            if (!query($db, 'SELECT 1 FROM products WHERE id = ?', [$id])->fetch()) {
                reply(404, ['error' => 'product_not_found']);
            }
            $rows = query($db, 'SELECT * FROM movements WHERE product_id = ? ORDER BY id DESC LIMIT 100', [$id])->fetchAll();
            reply(200, array_map('normalizeMovement', $rows));
        }
        header('Allow: GET, POST');
        reply(405, ['error' => 'method_not_allowed']);
    }
    reply(404, ['error' => 'route_not_found']);
} catch (Throwable $error) {
    if ($db?->inTransaction()) {
        $db->rollBack();
    }
    error_log('Lagerverwaltung: ' . get_class($error) . ' (' . $error->getCode() . ')');
    reply(500, ['error' => 'server_error']);
}
