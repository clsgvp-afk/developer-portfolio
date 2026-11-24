<?php

declare(strict_types=1);

function respond(int $status, array $data): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
}

if (parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) !== '/products') {
    respond(404, ['error' => 'Route not found']);
    return;
}
if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    header('Allow: GET');
    respond(405, ['error' => 'Only GET is supported']);
    return;
}

$category = $_GET['category'] ?? '';
$query = $_GET['q'] ?? '';

if (!is_string($category) || !is_string($query)) {
    respond(400, ['error' => 'category and q must be strings']);
    return;
}

$products = [
    ['id' => 1, 'name' => 'Desk Lamp', 'category' => 'lighting'],
    ['id' => 2, 'name' => 'Notebook', 'category' => 'stationery'],
    ['id' => 3, 'name' => 'Floor Lamp', 'category' => 'lighting'],
];

$matches = array_values(array_filter($products, static function (array $item) use ($category, $query): bool {
    $matchesCategory = $category === '' || strcasecmp($category, $item['category']) === 0;
    return $matchesCategory && ($query === '' || stripos($item['name'], $query) !== false);
}));

respond(200, ['count' => count($matches), 'products' => $matches]);
