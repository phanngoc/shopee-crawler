# Shopee Crawler — Điện Thoại & Phụ Kiện

Go pipeline crawl danh sách sản phẩm từ category **Điện Thoại & Phụ Kiện** trên Shopee VN.

## Cách hoạt động

Dùng **go-rod + stealth** để mở browser thật, intercept request `api/v4/search/search_items` mà Shopee tự gọi khi load trang, parse JSON response và lưu vào SQLite.

```
Browser (go-rod + stealth)
        │
        ▼
shopee.vn/Điện-Thoại-Phụ-Kiện-cat.11036030
        │
        ├─ Hijack: */api/v4/search/search_items*
        │         └─ Parse JSON → []Product
        │
        ▼
   SQLite (shopee.db)
```

## Schema SQLite

```sql
products (
    item_id, shop_id, name,
    price_min, price_max, price,  -- VND
    stock, sold, liked_count, comment_count,
    rating_star, rating_count,
    shop_name, brand, location,
    image_url, item_url,
    cat_id, cat_name, page_number,
    crawled_at, updated_at
)
```

## Build & Run

```bash
# Build
CGO_ENABLED=1 go build -o shopee-crawler .

# Chạy (cần Xvfb hoặc display thật)
./shopee-crawler --display :99

# Headless (không cần display)
./shopee-crawler --display ""

# Tùy chọn
./shopee-crawler --max-pages 50 --db custom.db --reset
```

## Options

| Flag | Default | Mô tả |
|------|---------|-------|
| `--max-pages` | 100 | Số trang tối đa (60 sp/trang) |
| `--db` | shopee.db | Path SQLite |
| `--display` | :99 | X display (empty = headless) |
| `--resume` | true | Tiếp tục từ checkpoint |
| `--reset` | false | Xóa progress, crawl lại từ đầu |
