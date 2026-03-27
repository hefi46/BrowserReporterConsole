package main

import (
	"database/sql"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

// WebKit timestamps: microseconds since 1601-01-01 00:00:00 UTC
const webkitEpochDiff = 11644473600 // seconds between 1601-01-01 and 1970-01-01

// Visit represents a single browser history entry.
type Visit struct {
	URL             string
	Title           string
	WebkitTimestamp int64  // raw WebKit timestamp for state tracking
	EpochMS         int64  // Unix epoch milliseconds for the API
	Browser         string // "chrome" or "edge"
	Profile         string // "Default", "Profile 1", etc.
}

func webkitToEpochMS(webkitTS int64) int64 {
	return (webkitTS/1_000_000 - webkitEpochDiff) * 1000
}

// maxHistoryWebkit returns the WebKit timestamp for maxDays ago.
func maxHistoryWebkit(maxDays int) int64 {
	cutoff := time.Now().UTC().Add(-time.Duration(maxDays) * 24 * time.Hour)
	epochSec := cutoff.Unix()
	return (epochSec + webkitEpochDiff) * 1_000_000
}

// browserDataDir returns the User Data directory for a browser.
func browserDataDir(browser string) string {
	localAppData := os.Getenv("LOCALAPPDATA")
	if localAppData == "" {
		return ""
	}
	switch browser {
	case "chrome":
		return filepath.Join(localAppData, "Google", "Chrome", "User Data")
	case "edge":
		return filepath.Join(localAppData, "Microsoft", "Edge", "User Data")
	default:
		return ""
	}
}

// profileEntry holds the profile directory name and History path.
type profileEntry struct {
	name    string // "Default", "Profile 1", etc.
	dbPath  string
}

// findHistoryPaths finds all History database files across browser profiles.
func findHistoryPaths(dataDir string) []profileEntry {
	var entries []profileEntry
	if dataDir == "" {
		return entries
	}

	// Default profile
	defaultPath := filepath.Join(dataDir, "Default", "History")
	if info, err := os.Stat(defaultPath); err == nil && !info.IsDir() {
		entries = append(entries, profileEntry{name: "Default", dbPath: defaultPath})
	}

	// Additional profiles (Profile 1, Profile 2, etc.)
	dirEntries, err := os.ReadDir(dataDir)
	if err != nil {
		return entries
	}
	for _, e := range dirEntries {
		if e.IsDir() && strings.HasPrefix(e.Name(), "Profile ") {
			p := filepath.Join(dataDir, e.Name(), "History")
			if info, err := os.Stat(p); err == nil && !info.IsDir() {
				entries = append(entries, profileEntry{name: e.Name(), dbPath: p})
			}
		}
	}
	return entries
}

// copyFile copies src to dst.
func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}

// queryHistory copies the History DB to a temp file and queries visits.
func queryHistory(dbPath string, sinceWebkit int64, browser, profile string) []Visit {
	tmpFile, err := os.CreateTemp("", "br_history_*.sqlite")
	if err != nil {
		logger.Printf("ERROR creating temp file: %v", err)
		return nil
	}
	tmpPath := tmpFile.Name()
	tmpFile.Close()
	defer os.Remove(tmpPath)

	if err := copyFile(dbPath, tmpPath); err != nil {
		logger.Printf("ERROR copying history db %s: %v", dbPath, err)
		return nil
	}

	db, err := sql.Open("sqlite", fmt.Sprintf("file:%s?mode=ro", tmpPath))
	if err != nil {
		logger.Printf("ERROR opening sqlite %s: %v", tmpPath, err)
		return nil
	}
	defer db.Close()

	rows, err := db.Query(`
		SELECT urls.url, urls.title, visits.visit_time
		FROM visits
		INNER JOIN urls ON visits.url = urls.id
		WHERE visits.visit_time > ?
		ORDER BY visits.visit_time ASC
	`, sinceWebkit)
	if err != nil {
		logger.Printf("ERROR querying history: %v", err)
		return nil
	}
	defer rows.Close()

	var visits []Visit
	for rows.Next() {
		var url, title sql.NullString
		var vt int64
		if err := rows.Scan(&url, &title, &vt); err != nil {
			logger.Printf("ERROR scanning row: %v", err)
			continue
		}
		visits = append(visits, Visit{
			URL:             url.String,
			Title:           title.String,
			WebkitTimestamp: vt,
			EpochMS:         webkitToEpochMS(vt),
			Browser:         browser,
			Profile:         profile,
		})
	}
	return visits
}

// CollectVisits collects all visits from a browser since the given WebKit timestamp.
func CollectVisits(browser string, sinceWebkit int64, maxDays int) []Visit {
	dataDir := browserDataDir(browser)
	if dataDir == "" {
		logger.Printf("WARN LOCALAPPDATA not set, skipping %s", browser)
		return nil
	}

	cutoff := maxHistoryWebkit(maxDays)
	if sinceWebkit > cutoff {
		cutoff = sinceWebkit
	}

	profiles := findHistoryPaths(dataDir)
	if len(profiles) == 0 {
		logger.Printf("INFO no %s history databases found", browser)
		return nil
	}

	var all []Visit
	for _, p := range profiles {
		logger.Printf("INFO reading %s history: %s (profile: %s)", browser, p.dbPath, p.name)
		visits := queryHistory(p.dbPath, cutoff, browser, p.name)
		all = append(all, visits...)
	}
	logger.Printf("INFO collected %d visits from %s", len(all), browser)
	return all
}
