package db

import (
	"database/sql"
	"fmt"

	_ "github.com/mattn/go-sqlite3"
)

type Product struct {
	ItemID       int64
	ShopID       int64
	Name         string
	PriceMin     int64 // in VND * 100000
	PriceMax     int64
	Price        int64
	Currency     string
	Stock        int64
	Sold         int64
	LikedCount   int64
	CommentCount int64
	RatingStar   float64
	RatingCount  int64
	ShopName     string
	Brand        string
	Location     string
	ImageURL     string
	ItemURL      string
	CatID        int64
	CatName      string
	PageNumber   int
}

type DB struct {
	conn *sql.DB
}

func Open(path string) (*DB, error) {
	conn, err := sql.Open("sqlite3", path+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	return &DB{conn: conn}, nil
}

func (d *DB) Close() { d.conn.Close() }

func (d *DB) InitSchema() error {
	_, err := d.conn.Exec(`
	CREATE TABLE IF NOT EXISTS products (
		id            INTEGER PRIMARY KEY AUTOINCREMENT,
		item_id       INTEGER NOT NULL UNIQUE,
		shop_id       INTEGER,
		name          TEXT,
		price_min     INTEGER,
		price_max     INTEGER,
		price         INTEGER,
		currency      TEXT,
		stock         INTEGER,
		sold          INTEGER,
		liked_count   INTEGER,
		comment_count INTEGER,
		rating_star   REAL,
		rating_count  INTEGER,
		shop_name     TEXT,
		brand         TEXT,
		location      TEXT,
		image_url     TEXT,
		item_url      TEXT,
		cat_id        INTEGER,
		cat_name      TEXT,
		page_number   INTEGER,
		crawled_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
		updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
	);

	CREATE TABLE IF NOT EXISTS crawl_sessions (
		id          INTEGER PRIMARY KEY AUTOINCREMENT,
		start_url   TEXT,
		pages       INTEGER DEFAULT 0,
		total       INTEGER DEFAULT 0,
		status      TEXT DEFAULT 'running',
		error       TEXT,
		started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
		finished_at DATETIME
	);

	CREATE TABLE IF NOT EXISTS crawl_progress (
		id        INTEGER PRIMARY KEY CHECK (id = 1),
		last_page INTEGER DEFAULT 0,
		updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
	);
	`)
	return err
}

func (d *DB) UpsertProduct(p Product) (isNew bool, err error) {
	var existing int64
	err = d.conn.QueryRow(`SELECT id FROM products WHERE item_id = ?`, p.ItemID).Scan(&existing)
	if err == sql.ErrNoRows {
		_, err = d.conn.Exec(`
			INSERT INTO products
				(item_id,shop_id,name,price_min,price_max,price,currency,stock,sold,
				 liked_count,comment_count,rating_star,rating_count,shop_name,brand,
				 location,image_url,item_url,cat_id,cat_name,page_number)
			VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
			p.ItemID, p.ShopID, p.Name, p.PriceMin, p.PriceMax, p.Price, p.Currency,
			p.Stock, p.Sold, p.LikedCount, p.CommentCount, p.RatingStar, p.RatingCount,
			p.ShopName, p.Brand, p.Location, p.ImageURL, p.ItemURL,
			p.CatID, p.CatName, p.PageNumber,
		)
		return true, err
	}
	if err != nil {
		return false, err
	}
	// Update
	_, err = d.conn.Exec(`
		UPDATE products SET
			name=?, price_min=?, price_max=?, price=?, stock=?, sold=?,
			liked_count=?, comment_count=?, rating_star=?, rating_count=?,
			shop_name=?, brand=?, location=?, updated_at=CURRENT_TIMESTAMP
		WHERE item_id=?`,
		p.Name, p.PriceMin, p.PriceMax, p.Price, p.Stock, p.Sold,
		p.LikedCount, p.CommentCount, p.RatingStar, p.RatingCount,
		p.ShopName, p.Brand, p.Location, p.ItemID,
	)
	return false, err
}

func (d *DB) CountProducts() (int64, error) {
	var n int64
	err := d.conn.QueryRow(`SELECT COUNT(*) FROM products`).Scan(&n)
	return n, err
}

func (d *DB) GetLastPage() (int, error) {
	var page int
	err := d.conn.QueryRow(`SELECT COALESCE(last_page,0) FROM crawl_progress WHERE id=1`).Scan(&page)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return page, err
}

func (d *DB) UpdateProgress(page int) error {
	_, err := d.conn.Exec(`
		INSERT INTO crawl_progress (id, last_page) VALUES (1, ?)
		ON CONFLICT(id) DO UPDATE SET last_page=excluded.last_page, updated_at=CURRENT_TIMESTAMP`,
		page)
	return err
}

func (d *DB) ResetProgress() error {
	_, err := d.conn.Exec(`DELETE FROM crawl_progress`)
	return err
}

func (d *DB) StartSession(url string) (int64, error) {
	res, err := d.conn.Exec(`INSERT INTO crawl_sessions (start_url) VALUES (?)`, url)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

func (d *DB) FinishSession(id int64, pages, total int, status, errText string) {
	d.conn.Exec(`UPDATE crawl_sessions SET pages=?,total=?,status=?,error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?`,
		pages, total, status, errText, id)
}
