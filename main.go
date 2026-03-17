package main

import (
	"flag"
	"fmt"
	"log"
	"math/rand"
	"os"
	"os/signal"
	"syscall"
	"time"

	"shopee-crawler/crawler"
	"shopee-crawler/db"
)

func main() {
	rand.Seed(time.Now().UnixNano())

	resume  := flag.Bool("resume", true, "Resume from last checkpoint")
	reset   := flag.Bool("reset", false, "Clear progress and start fresh")
	maxPages := flag.Int("max-pages", 100, "Maximum pages to crawl (60 items/page)")
	dbPath  := flag.String("db", "shopee.db", "Path to SQLite database")
	display := flag.String("display", ":99", "X display (empty = headless)")
	flag.Parse()

	if *reset {
		*resume = false
	}

	database, err := db.Open(*dbPath)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}
	defer database.Close()

	if err := database.InitSchema(); err != nil {
		log.Fatalf("init schema: %v", err)
	}

	if *reset {
		if err := database.ResetProgress(); err != nil {
			log.Fatalf("reset: %v", err)
		}
		log.Println("Progress reset.")
	}

	startPage := 1
	if *resume {
		last, err := database.GetLastPage()
		if err != nil {
			log.Fatalf("get last page: %v", err)
		}
		if last > 0 {
			startPage = last + 1
			log.Printf("Resuming from page %d", startPage)
		}
	}

	sessionID, err := database.StartSession(crawler.CategoryURL)
	if err != nil {
		log.Fatalf("start session: %v", err)
	}

	c, err := crawler.New(*display)
	if err != nil {
		log.Fatalf("init crawler: %v", err)
	}
	defer c.Close()

	interrupted := false
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("\nInterrupted! Saving progress...")
		interrupted = true
	}()

	started := time.Now()
	totalNew, totalUpdated, pagesOK := 0, 0, 0

	for page := startPage; page <= *maxPages; page++ {
		if interrupted {
			break
		}

		log.Printf("[Page %d/%d] Crawling...", page, *maxPages)
		result, err := c.CrawlPage(page)
		if err != nil {
			log.Printf("  Error: %v — retrying once...", err)
			crawler.RandomDelay()
			result, err = c.CrawlPage(page)
			if err != nil {
				log.Printf("  Retry failed: %v", err)
				database.FinishSession(sessionID, pagesOK, 0, "error", err.Error())
				os.Exit(1)
			}
		}

		if len(result.Products) == 0 {
			log.Printf("  Page %d returned 0 products — stopping.", page)
			break
		}

		pageNew, pageUpdated := 0, 0
		for _, p := range result.Products {
			isNew, err := database.UpsertProduct(p)
			if err != nil {
				log.Printf("  upsert %d: %v", p.ItemID, err)
				continue
			}
			if isNew {
				pageNew++
			} else {
				pageUpdated++
			}
		}

		totalNew += pageNew
		totalUpdated += pageUpdated
		pagesOK++

		database.UpdateProgress(page)

		total, _ := database.CountProducts()
		log.Printf("  → %d total | +%d new | %d updated", total, pageNew, pageUpdated)

		if !result.HasNext {
			log.Println("  Last page reached.")
			break
		}

		if !interrupted {
			crawler.RandomDelay()
		}
	}

	status := "done"
	if interrupted {
		status = "interrupted"
	}
	totalProducts, _ := database.CountProducts()
	database.FinishSession(sessionID, pagesOK, int(totalProducts), status, "")

	fmt.Println("\n========================================")
	fmt.Printf("Status:    %s\n", status)
	fmt.Printf("Pages:     %d\n", pagesOK)
	fmt.Printf("Products:  %d\n", totalProducts)
	fmt.Printf("New:       %d\n", totalNew)
	fmt.Printf("Updated:   %d\n", totalUpdated)
	fmt.Printf("Duration:  %s\n", time.Since(started).Round(time.Second))
	fmt.Println("========================================")
}
