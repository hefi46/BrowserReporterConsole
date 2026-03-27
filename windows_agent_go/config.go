package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Config holds the decrypted agent configuration.
type Config struct {
	ServerURL                string `json:"server_url"`
	CollectionIntervalMin    int    `json:"collection_interval_minutes"`
	MaxHistoryDays           int    `json:"max_history_days"`
	MonitoredStartTime       string `json:"monitored_start_time"`
	MonitoredEndTime         string `json:"monitored_end_time"`
	EnableChrome             bool   `json:"enable_chrome"`
	EnableEdge               bool   `json:"enable_edge"`
	LogMaxMB                 int    `json:"log_max_mb"`
	LogRollCount             int    `json:"log_roll_count"`
}

// DefaultConfig returns sensible defaults matching the server's client_config page.
func DefaultConfig() Config {
	return Config{
		ServerURL:             "http://localhost:8000",
		CollectionIntervalMin: 5,
		MaxHistoryDays:        30,
		MonitoredStartTime:    "08:00",
		MonitoredEndTime:      "23:59",
		EnableChrome:          true,
		EnableEdge:            true,
		LogMaxMB:              10,
		LogRollCount:          3,
	}
}

// secureConfigEnvelope matches the JSON structure of secureconfig.json.
type secureConfigEnvelope struct {
	EncryptedData string `json:"encrypted_data"`
	IV            string `json:"iv"`
	Checksum      string `json:"checksum"`
}

// deriveKey replicates backend/utils.py: SHA-256 of master key string → 32-byte AES key.
func deriveKey() []byte {
	masterKey := os.Getenv("ENCRYPTION_MASTER_KEY")
	if masterKey == "" {
		masterKey = "BrowserReporter2024!MasterKey"
	}
	h := sha256.Sum256([]byte(masterKey))
	return h[:]
}

// pkcs7Unpad removes PKCS#7 padding.
func pkcs7Unpad(data []byte, blockSize int) ([]byte, error) {
	if len(data) == 0 || len(data)%blockSize != 0 {
		return nil, fmt.Errorf("invalid padded data length")
	}
	padLen := int(data[len(data)-1])
	if padLen == 0 || padLen > blockSize || padLen > len(data) {
		return nil, fmt.Errorf("invalid padding value %d", padLen)
	}
	for i := len(data) - padLen; i < len(data); i++ {
		if data[i] != byte(padLen) {
			return nil, fmt.Errorf("invalid padding bytes")
		}
	}
	return data[:len(data)-padLen], nil
}

// decryptSecureConfig decrypts AES-256-CBC encrypted config, matching backend/utils.py.
func decryptSecureConfig(envelope secureConfigEnvelope) (Config, error) {
	key := deriveKey()

	ct, err := base64.StdEncoding.DecodeString(envelope.EncryptedData)
	if err != nil {
		return Config{}, fmt.Errorf("decode encrypted_data: %w", err)
	}
	iv, err := base64.StdEncoding.DecodeString(envelope.IV)
	if err != nil {
		return Config{}, fmt.Errorf("decode iv: %w", err)
	}

	block, err := aes.NewCipher(key)
	if err != nil {
		return Config{}, fmt.Errorf("aes cipher: %w", err)
	}
	if len(ct)%aes.BlockSize != 0 {
		return Config{}, fmt.Errorf("ciphertext not multiple of block size")
	}

	mode := cipher.NewCBCDecrypter(block, iv)
	plainPadded := make([]byte, len(ct))
	mode.CryptBlocks(plainPadded, ct)

	plain, err := pkcs7Unpad(plainPadded, aes.BlockSize)
	if err != nil {
		return Config{}, fmt.Errorf("unpad: %w", err)
	}

	// Verify SHA-256 checksum
	checksum := fmt.Sprintf("%x", sha256.Sum256(plain))
	if checksum != envelope.Checksum {
		return Config{}, fmt.Errorf("checksum mismatch: got %s, want %s", checksum, envelope.Checksum)
	}

	cfg := DefaultConfig()
	if err := json.Unmarshal(plain, &cfg); err != nil {
		return Config{}, fmt.Errorf("unmarshal config: %w", err)
	}
	return cfg, nil
}

// exeDir returns the directory containing the running executable.
func exeDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

// LoadConfig reads and decrypts secureconfig.json from the exe directory.
// Falls back to defaults if the file is missing or decryption fails.
func LoadConfig() Config {
	configPath := filepath.Join(exeDir(), "secureconfig.json")

	data, err := os.ReadFile(configPath)
	if err != nil {
		logger.Printf("WARN secureconfig.json not found at %s, using defaults", configPath)
		return DefaultConfig()
	}

	var envelope secureConfigEnvelope
	if err := json.Unmarshal(data, &envelope); err != nil {
		logger.Printf("ERROR parsing secureconfig.json: %v — using defaults", err)
		return DefaultConfig()
	}

	cfg, err := decryptSecureConfig(envelope)
	if err != nil {
		logger.Printf("ERROR decrypting config: %v — using defaults", err)
		return DefaultConfig()
	}

	logger.Println("INFO config loaded and decrypted successfully")
	return cfg
}
