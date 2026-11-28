CREATE TABLE products (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku text UNIQUE NOT NULL,
    name text NOT NULL,
    stock integer NOT NULL DEFAULT 0 CHECK (stock BETWEEN 0 AND 1000000000),
    minimum integer NOT NULL CHECK (minimum >= 0)
);
CREATE TABLE movements (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id uuid UNIQUE NOT NULL,
    product_id integer NOT NULL REFERENCES products(id),
    delta integer NOT NULL CHECK (delta BETWEEN -1000000 AND 1000000 AND delta <> 0),
    stock_after integer NOT NULL CHECK (stock_after >= 0),
    note text NOT NULL CHECK (length(note) <= 160),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX movements_by_product ON movements(product_id, id DESC);
