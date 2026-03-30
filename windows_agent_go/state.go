package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// stateDir returns %LOCALAPPDATA%\BrowserReporter.
func stateDir() string {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		base, _ = os.UserHomeDir()
	}
	return filepath.Join(base, "BrowserReporter")
}

func stateFilePath() string {
	return filepath.Join(stateDir(), "state.json")
}

func loadState() map[string]int64 {
	data, err := os.ReadFile(stateFilePath())
	if err != nil {
		return make(map[string]int64)
	}
	var state map[string]int64
	if err := json.Unmarshal(data, &state); err != nil {
		logger.Printf("WARN could not parse state: %v", err)
		return make(map[string]int64)
	}
	return state
}

func saveState(state map[string]int64) {
	if err := os.MkdirAll(stateDir(), 0o755); err != nil {
		logger.Printf("ERROR creating state dir: %v", err)
		return
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		logger.Printf("ERROR marshaling state: %v", err)
		return
	}
	if err := os.WriteFile(stateFilePath(), data, 0o644); err != nil {
		logger.Printf("ERROR writing state: %v", err)
	}
}

// GetLastSent returns the last-sent WebKit timestamp for a browser key.
func GetLastSent(browser string) int64 {
	state := loadState()
	return state["last_sent_"+browser]
}

// SetLastSent updates the last-sent WebKit timestamp for a browser key.
func SetLastSent(browser string, webkitTS int64) {
	state := loadState()
	state["last_sent_"+browser] = webkitTS
	saveState(state)
}

func cachedServerPath() string {
	return filepath.Join(stateDir(), "cached_server.txt")
}

// CacheServerURL persists the last known working server URL for config recovery.
func CacheServerURL(serverURL string) {
	os.MkdirAll(stateDir(), 0o755)
	if err := os.WriteFile(cachedServerPath(), []byte(serverURL), 0o644); err != nil {
		logger.Printf("WARN failed to cache server URL: %v", err)
	}
}

// GetCachedServerURL returns the last known working server URL, or "" if unavailable.
func GetCachedServerURL() string {
	data, err := os.ReadFile(cachedServerPath())
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}
