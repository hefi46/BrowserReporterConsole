package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

var httpClient = &http.Client{Timeout: 30 * time.Second}

// versionResponse matches GET /api/agent/version.
type versionResponse struct {
	Version string `json:"version"`
}

// CheckVersion checks the server for the latest agent version.
// Returns the remote version string, or empty string on failure.
func CheckVersion(serverURL string) string {
	resp, err := httpClient.Get(serverURL + "/api/agent/version")
	if err != nil {
		logger.Printf("WARN version check failed: %v", err)
		return ""
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		logger.Printf("WARN version check returned %d", resp.StatusCode)
		return ""
	}

	var vr versionResponse
	if err := json.NewDecoder(resp.Body).Decode(&vr); err != nil {
		logger.Printf("WARN version check parse failed: %v", err)
		return ""
	}
	return vr.Version
}

// reportPayload matches the server's ReportIn schema.
type reportPayload struct {
	Username string      `json:"Username"`
	UserInfo userInfoIn  `json:"UserInfo"`
	Visits   []visitIn   `json:"Visits"`
}

type userInfoIn struct {
	Username    string  `json:"Username"`
	DisplayName *string `json:"DisplayName"`
	Email       *string `json:"Email"`
}

type visitIn struct {
	URL            string  `json:"Url"`
	Title          string  `json:"Title"`
	VisitTime      int64   `json:"VisitTime"`
	ComputerName   string  `json:"ComputerName"`
	Source         string  `json:"Source,omitempty"`
	BrowserProfile string  `json:"BrowserProfile,omitempty"`
}

// SendVisits POSTs collected visits to /api/reports/data.
func SendVisits(serverURL, username, computerName string, visits []Visit) bool {
	payload := reportPayload{
		Username: username,
		UserInfo: userInfoIn{
			Username: username,
		},
		Visits: make([]visitIn, len(visits)),
	}

	for i, v := range visits {
		browserSource := "windows_agent"
		profileName := v.Profile
		if v.Browser == "edge" {
			browserSource = "windows_agent_edge"
		}
		payload.Visits[i] = visitIn{
			URL:            v.URL,
			Title:          v.Title,
			VisitTime:      v.EpochMS,
			ComputerName:   computerName,
			Source:         browserSource,
			BrowserProfile: profileName,
		}
	}

	body, err := json.Marshal(payload)
	if err != nil {
		logger.Printf("ERROR marshaling payload: %v", err)
		return false
	}

	url := serverURL + "/api/reports/data"
	resp, err := httpClient.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		logger.Printf("ERROR sending visits: %v", err)
		return false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(resp.Body)
		logger.Printf("ERROR server returned %d: %s", resp.StatusCode, string(respBody))
		return false
	}

	logger.Printf("INFO sent %d visits successfully", len(visits))
	return true
}

// FetchConfig downloads secureconfig.json from the server and overwrites the local copy.
// Called by bootstrap flow, but also available for the daemon to refresh on startup.
func FetchConfig(serverURL string) error {
	resp, err := httpClient.Get(serverURL + "/api/agent/config")
	if err != nil {
		return fmt.Errorf("fetch config: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("fetch config returned %d", resp.StatusCode)
	}

	// We don't overwrite from the daemon — this is a helper if needed.
	// The bootstrap.ps1 handles config download.
	return nil
}
