package crawler

import (
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"os"
	"sync"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
	"github.com/go-rod/stealth"

	"shopee-crawler/db"
)

const (
	CategoryURL = "https://shopee.vn/%C4%90i%E1%BB%87n-Tho%E1%BA%A1i-Ph%E1%BB%A5-Ki%E1%BB%87n-cat.11036030"
	PageSize    = 60
	CategoryID  = 11036030
)

var userAgents = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
}

// shopee API response shapes
type searchResponse struct {
	Items []struct {
		ItemBasic itemBasic `json:"item_basic"`
	} `json:"items"`
	TotalCount int `json:"total_count"`
}

type itemBasic struct {
	ItemID       int64   `json:"itemid"`
	ShopID       int64   `json:"shopid"`
	Name         string  `json:"name"`
	Price        int64   `json:"price"`
	PriceMin     int64   `json:"price_min"`
	PriceMax     int64   `json:"price_max"`
	Currency     string  `json:"currency"`
	Stock        int64   `json:"stock"`
	Sold         int64   `json:"sold"`
	LikedCount   int64   `json:"liked_count"`
	CommentCount int64   `json:"comment_count"`
	Brand        string  `json:"brand"`
	ShopName     string  `json:"shop_name"`
	ShopLocation string  `json:"shop_location"`
	CatID        int64   `json:"catid"`
	Images       []string `json:"images"`
	ItemRating   struct {
		RatingStar  float64 `json:"rating_star"`
		RatingCount []int64 `json:"rating_count"`
	} `json:"item_rating"`
}

type Crawler struct {
	browser *rod.Browser
	display string
}

type PageResult struct {
	Products   []db.Product
	TotalCount int
	HasNext    bool
}

func New(display string) (*Crawler, error) {
	chromePath, found := launcher.LookPath()
	if !found {
		return nil, fmt.Errorf("chrome/chromium not found")
	}
	if display != "" {
		os.Setenv("DISPLAY", display)
	}
	if os.Getenv("HOME") == "" {
		os.Setenv("HOME", "/tmp")
	}

	headless := display == ""
	l := launcher.New().
		Bin(chromePath).
		Headless(headless).
		Set("disable-blink-features", "AutomationControlled").
		Set("no-sandbox").
		Set("disable-dev-shm-usage").
		Set("disable-web-security").
		Set("lang", "vi-VN")

	controlURL, err := l.Launch()
	if err != nil {
		return nil, fmt.Errorf("launch browser: %w", err)
	}
	browser := rod.New().ControlURL(controlURL)
	if err := browser.Connect(); err != nil {
		return nil, fmt.Errorf("connect browser: %w", err)
	}
	return &Crawler{browser: browser, display: display}, nil
}

func (c *Crawler) Close() {
	if c.browser != nil {
		c.browser.Close()
	}
}

// CrawlPage fetches one page of products by intercepting Shopee's internal API call.
// newest = page * PageSize (offset)
func (c *Crawler) CrawlPage(page int) (*PageResult, error) {
	pageURL := fmt.Sprintf("%s?page=%d&sortBy=pop", CategoryURL, page-1)

	ua := userAgents[rand.Intn(len(userAgents))]

	pg, err := stealth.Page(c.browser)
	if err != nil {
		return nil, fmt.Errorf("create page: %w", err)
	}
	defer pg.Close()

	if err := pg.SetUserAgent(&proto.NetworkSetUserAgentOverride{UserAgent: ua}); err != nil {
		return nil, fmt.Errorf("set ua: %w", err)
	}

	// Intercept the search_items API response
	var (
		mu       sync.Mutex
		apiBody  []byte
		apiReady = make(chan struct{}, 1)
	)

	router := pg.HijackRequests()
	router.MustAdd("*/api/v4/search/search_items*", func(ctx *rod.Hijack) {
		ctx.MustLoadResponse()
		mu.Lock()
		apiBody = []byte(ctx.Response.Body())
		mu.Unlock()
		select {
		case apiReady <- struct{}{}:
		default:
		}
	})
	go router.Run()
	defer router.Stop()

	if err := pg.Navigate(pageURL); err != nil {
		return nil, fmt.Errorf("navigate: %w", err)
	}

	// Wait for API interception or timeout
	select {
	case <-apiReady:
		// got it
	case <-time.After(25 * time.Second):
		// fallback: try scrolling to trigger lazy load
		pg.Mouse.Scroll(0, 500, 3)
		select {
		case <-apiReady:
		case <-time.After(15 * time.Second):
			return nil, fmt.Errorf("timeout waiting for Shopee API on page %d", page)
		}
	}

	mu.Lock()
	body := make([]byte, len(apiBody))
	copy(body, apiBody)
	mu.Unlock()

	return parseSearchResponse(body, page)
}

func parseSearchResponse(body []byte, page int) (*PageResult, error) {
	var resp searchResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("unmarshal response: %w (body: %.200s)", err, body)
	}

	products := make([]db.Product, 0, len(resp.Items))
	for _, item := range resp.Items {
		ib := item.ItemBasic
		if ib.ItemID == 0 {
			continue
		}

		imageURL := ""
		if len(ib.Images) > 0 {
			imageURL = fmt.Sprintf("https://cf.shopee.vn/file/%s", ib.Images[0])
		}

		totalRating := int64(0)
		for _, rc := range ib.ItemRating.RatingCount {
			totalRating += rc
		}

		itemURL := fmt.Sprintf("https://shopee.vn/product/%d/%d", ib.ShopID, ib.ItemID)

		products = append(products, db.Product{
			ItemID:       ib.ItemID,
			ShopID:       ib.ShopID,
			Name:         ib.Name,
			Price:        ib.Price / 100000,        // convert to VND
			PriceMin:     ib.PriceMin / 100000,
			PriceMax:     ib.PriceMax / 100000,
			Currency:     ib.Currency,
			Stock:        ib.Stock,
			Sold:         ib.Sold,
			LikedCount:   ib.LikedCount,
			CommentCount: ib.CommentCount,
			RatingStar:   ib.ItemRating.RatingStar,
			RatingCount:  totalRating,
			ShopName:     ib.ShopName,
			Brand:        ib.Brand,
			Location:     ib.ShopLocation,
			ImageURL:     imageURL,
			ItemURL:      itemURL,
			CatID:        ib.CatID,
			CatName:      "Điện Thoại & Phụ Kiện",
			PageNumber:   page,
		})
	}

	hasNext := len(products) >= PageSize
	log.Printf("  Parsed %d products from page %d (total_count=%d)", len(products), page, resp.TotalCount)
	return &PageResult{
		Products:   products,
		TotalCount: resp.TotalCount,
		HasNext:    hasNext,
	}, nil
}

func RandomDelay() {
	ms := 2000 + rand.Intn(3000)
	log.Printf("  Waiting %dms...", ms)
	time.Sleep(time.Duration(ms) * time.Millisecond)
}
