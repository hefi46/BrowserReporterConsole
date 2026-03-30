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

// logState holds the active log file handle and config for runtime rotation.
var logState struct {
	file     *os.File
	path     string
	maxBytes int64
	rollCnt  int
}

func setupLogging(cfg Config) *os.File {
	logDir := stateDir()
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "WARN cannot create log dir: %v\n", err)
	}

	logState.path = filepath.Join(logDir, "agent.log")
	logState.maxBytes = int64(cfg.LogMaxMB) * 1024 * 1024
	logState.rollCnt = cfg.LogRollCount

	rotateIfNeeded()

	f, err := os.OpenFile(logState.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "WARN cannot open log file: %v\n", err)
		logger = log.New(os.Stderr, "", log.LstdFlags)
		return nil
	}

	logState.file = f
	logger = log.New(f, "", log.LstdFlags)
	return f
}

// rotateIfNeeded checks the log file size and rotates if it exceeds the limit.
func rotateIfNeeded() {
	if logState.path == "" {
		return
	}
	info, err := os.Stat(logState.path)
	if err != nil || info.Size() <= logState.maxBytes {
		return
	}

	// Close current file before rotating
	if logState.file != nil {
		logState.file.Close()
	}

	for i := logState.rollCnt; i > 1; i-- {
		old := fmt.Sprintf("%s.%d", logState.path, i-1)
		newPath := fmt.Sprintf("%s.%d", logState.path, i)
		os.Rename(old, newPath)
	}
	os.Rename(logState.path, logState.path+".1")

	// Reopen the log file
	f, err := os.OpenFile(logState.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		logger = log.New(os.Stderr, "", log.LstdFlags)
		logState.file = nil
		return
	}
	logState.file = f
	logger = log.New(f, "", log.LstdFlags)
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

	intervalSec := cfg.CollectionIntervalSec
	if intervalSec < 30 {
		intervalSec = 30 // minimum 30 seconds
	} else if intervalSec > 300 {
		intervalSec = 300 // maximum 5 minutes
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
	outsideWindowLogged := false
	for {
		cycle++

		// Check log rotation each cycle
		rotateIfNeeded()

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
			if !outsideWindowLogged {
				logger.Printf("INFO outside monitoring window (%s-%s), sleeping", cfg.MonitoredStartTime, cfg.MonitoredEndTime)
				outsideWindowLogged = true
			}
			sleepWithSignal(sigChan, time.Duration(intervalSec)*time.Second)
			continue
		}
		outsideWindowLogged = false

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

		if len(allVisits) > 0 {
			if SendVisits(serverURL, username, computerName, allVisits) {
				for browser, ts := range maxWebkit {
					SetLastSent(browser, ts)
				}
				logger.Printf("INFO sent %d visits", len(allVisits))
			} else {
				logger.Printf("WARN send failed (%d visits), will retry next cycle", len(allVisits))
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
