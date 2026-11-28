BEGIN;
INSERT INTO products (sku, name, stock, minimum) VALUES
('EL-1040','USB-C Netzteil 65 W',5,3),
('EL-1080','Netzwerkkabel CAT 6 · 2 m',42,15),
('LG-2100','Versandkarton 300 × 200 mm',18,20),
('LG-2240','Etikettenrolle 100 × 150 mm',0,6);
INSERT INTO movements (request_id, product_id, delta, stock_after, note)
SELECT md5(sku)::uuid, id, stock, stock, 'Startbestand' FROM products WHERE stock > 0 ORDER BY sku;
COMMIT;
