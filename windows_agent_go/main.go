package main

import (
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// logger is the package-level logger used by all files.
var logger *log.Logger

func setupLogging(cfg Config) *os.File {
	logDir := stateDir()
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "WARN cannot create log dir: %v\n", err)
	}

	logPath := filepath.Join(logDir, "agent.log")

	// Rotate if log exceeds max size
	maxBytes := int64(cfg.LogMaxMB) * 1024 * 1024
	if info, err := os.Stat(logPath); err == nil && info.Size() > maxBytes {
		// Simple rotation: rename current → .1, drop older
		for i := cfg.LogRollCount; i > 1; i-- {
			old := fmt.Sprintf("%s.%d", logPath, i-1)
			new := fmt.Sprintf("%s.%d", logPath, i)
			os.Rename(old, new)
		}
		os.Rename(logPath, logPath+".1")
	}

	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "WARN cannot open log file: %v\n", err)
		logger = log.New(os.Stderr, "", log.LstdFlags)
		return nil
	}

	logger = log.New(f, "", log.LstdFlags)
	return f
}

// inTimeWindow checks if the current time is within the configured monitoring window.
func inTimeWindow(cfg Config) bool {
	now := time.Now()
	nowMinutes := now.Hour()*60 + now.Minute()

	startParts := strings.SplitN(cfg.MonitoredStartTime, ":", 2)
	endParts := strings.SplitN(cfg.MonitoredEndTime, ":", 2)

	startMin := parseTimeMinutes(startParts)
	endMin := parseTimeMinutes(endParts)

	if startMin <= endMin {
		return nowMinutes >= startMin && nowMinutes <= endMin
	}
	// Wraps midnight
	return nowMinutes >= startMin || nowMinutes <= endMin
}

func parseTimeMinutes(parts []string) int {
	if len(parts) != 2 {
		return 0
	}
	h, m := 0, 0
	fmt.Sscanf(parts[0], "%d", &h)
	fmt.Sscanf(parts[1], "%d", &m)
	return h*60 + m
}

const configRetryInterval = 5 * time.Minute

// loadConfigWithRetry attempts LoadConfig and retries every 5 minutes on failure.
// Returns successfully loaded config, or error if shutdown signal received.
func loadConfigWithRetry(sigChan <-chan os.Signal) (Config, error) {
	cfg, err := LoadConfig()
	if err == nil {
		return cfg, nil
	}

	logger.Printf("ERROR config unavailable: %v", err)
	logger.Printf("INFO entering dormant mode, will retry every %v", configRetryInterval)

	for {
		select {
		case <-sigChan:
			logger.Println("INFO received shutdown signal during dormant mode")
			return Config{}, fmt.Errorf("shutdown during config retry")
		case <-time.After(configRetryInterval):
			cfg, err = LoadConfig()
			if err == nil {
				logger.Println("INFO config now available, resuming normal operation")
				return cfg, nil
			}
			logger.Printf("WARN config still unavailable: %v", err)
		}
	}
}

func main() {
	// Temporary logger until config is loaded
	logger = log.New(os.Stderr, "", log.LstdFlags)

	// Graceful shutdown on SIGTERM / SIGINT / console close
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGTERM, syscall.SIGINT)

	cfg, err := loadConfigWithRetry(sigChan)
	if err != nil {
		return
	}

	logFile := setupLogging(cfg)
	if logFile != nil {
		defer logFile.Close()
	}

	serverURL := strings.TrimRight(cfg.ServerURL, "/")
	logger.Printf("INFO BrowserReporter Agent v%s starting (daemon mode)", Version)

	intervalSec := cfg.CollectionIntervalMin * 60
	if intervalSec < 60 {
		intervalSec = 60 // minimum 1 minute
	}

	username := os.Getenv("USERNAME")
	if username == "" {
		username = "unknown"
	}
	computerName := os.Getenv("COMPUTERNAME")
	if computerName == "" {
		computerName = "unknown"
	}
	logger.Printf("INFO user: %s, computer: %s", username, computerName)

	// Main daemon loop
	cycle := 0
	for {
		cycle++

		// Check for shutdown signal (non-blocking)
		select {
		case <-sigChan:
			logger.Println("INFO received shutdown signal, exiting")
			return
		default:
		}

		// Version check — exit if update available so bootstrap can pull new version
		remoteVersion := CheckVersion(serverURL)
		if remoteVersion != "" && remoteVersion != Version {
			logger.Printf("INFO update available: local=%s remote=%s — exiting for update", Version, remoteVersion)
			return
		}

		// Time window check — sleep and retry if outside hours (don't exit)
		if !inTimeWindow(cfg) {
			logger.Printf("INFO outside monitoring window (%s-%s), sleeping", cfg.MonitoredStartTime, cfg.MonitoredEndTime)
			sleepWithSignal(sigChan, time.Duration(intervalSec)*time.Second)
			continue
		}

		// Collect browser history
		var browsers []string
		if cfg.EnableChrome {
			browsers = append(browsers, "chrome")
		}
		if cfg.EnableEdge {
			browsers = append(browsers, "edge")
		}

		var allVisits []Visit
		maxWebkit := make(map[string]int64)

		for _, browser := range browsers {
			since := GetLastSent(browser)
			visits := CollectVisits(browser, since, cfg.MaxHistoryDays)
			if len(visits) > 0 {
				allVisits = append(allVisits, visits...)
				for _, v := range visits {
					if v.WebkitTimestamp > maxWebkit[browser] {
						maxWebkit[browser] = v.WebkitTimestamp
					}
				}
			}
		}

		if len(allVisits) == 0 {
			logger.Printf("INFO cycle %d: no new visits", cycle)
		} else {
			logger.Printf("INFO cycle %d: %d visits to send", cycle, len(allVisits))

			if SendVisits(serverURL, username, computerName, allVisits) {
				for browser, ts := range maxWebkit {
					SetLastSent(browser, ts)
				}
				logger.Printf("INFO cycle %d: state updated", cycle)
			} else {
				logger.Printf("WARN cycle %d: send failed, will retry next cycle", cycle)
			}
		}

		// Sleep until next cycle
		sleepWithSignal(sigChan, time.Duration(intervalSec)*time.Second)
	}
}

// sleepWithSignal sleeps for the given duration but wakes early on shutdown signal.
func sleepWithSignal(sigChan <-chan os.Signal, d time.Duration) {
	select {
	case <-sigChan:
		logger.Println("INFO received shutdown signal during sleep, exiting")
		os.Exit(0)
	case <-time.After(d):
	}
}
