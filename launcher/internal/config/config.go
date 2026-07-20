package config

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const minimumSecretBytes = 32

type Config struct {
	BindAddress      string
	SecretFile       string
	HeadHealthURL    string
	ComposeFile      string
	ComposeProject   string
	StatePath        string
	LockPath         string
	LogPath          string
	GHCRNamespace    string
	GHCRToken        string
	DockerRetry      time.Duration
	DockerRetryMax   time.Duration
	HealthInterval   time.Duration
	HealthTimeout    time.Duration
	ShutdownTimeout  time.Duration
	ReplayRetention  time.Duration
	RequestRetention time.Duration
	LogMaxBytes      int64
	LogBackups       int
}

func Load() (Config, error) {
	port, err := uintEnv("LAUNCHER_IPC_PORT", 9700, 1, 65535)
	if err != nil {
		return Config{}, err
	}
	headPort, err := uintEnv("HEAD_IPC_HOST_PORT", 9800, 1, 65535)
	if err != nil {
		return Config{}, err
	}
	dockerRetry, err := durationEnv("LAUNCHER_DOCKER_RETRY_INTERVAL_SEC", 15, false)
	if err != nil {
		return Config{}, err
	}
	dockerMax, err := durationEnv("LAUNCHER_DOCKER_RETRY_MAX_SEC", 0, true)
	if err != nil {
		return Config{}, err
	}
	healthInterval, err := durationEnv("LAUNCHER_HEALTHCHECK_INTERVAL_SEC", 10, false)
	if err != nil {
		return Config{}, err
	}
	healthTimeout, err := durationEnv("LAUNCHER_HEALTHCHECK_TIMEOUT_SEC", 300, false)
	if err != nil {
		return Config{}, err
	}

	bindHost := env("LAUNCHER_IPC_BIND", "0.0.0.0")
	if net.ParseIP(bindHost) == nil && bindHost != "localhost" {
		return Config{}, fmt.Errorf("LAUNCHER_IPC_BIND must be an IP address or localhost")
	}
	composeFile, err := absolute(env("LAUNCHER_COMPOSE_FILE", "docker-compose.yml"))
	if err != nil {
		return Config{}, fmt.Errorf("LAUNCHER_COMPOSE_FILE: %w", err)
	}
	statePath, err := absolute(env("LAUNCHER_VERSION_HISTORY_PATH", "./launcher_state.json"))
	if err != nil {
		return Config{}, fmt.Errorf("LAUNCHER_VERSION_HISTORY_PATH: %w", err)
	}
	lockPath, err := absolute(env("LAUNCHER_LOCK_PATH", "./launcher.lock"))
	if err != nil {
		return Config{}, fmt.Errorf("LAUNCHER_LOCK_PATH: %w", err)
	}
	logPath, err := absolute(env("LAUNCHER_LOG_PATH", "./logs/launcher.log"))
	if err != nil {
		return Config{}, fmt.Errorf("LAUNCHER_LOG_PATH: %w", err)
	}
	secretFile := strings.TrimSpace(os.Getenv("LAUNCHER_IPC_SECRET_FILE"))
	if secretFile != "" {
		secretFile, err = absolute(secretFile)
		if err != nil {
			return Config{}, fmt.Errorf("LAUNCHER_IPC_SECRET_FILE: %w", err)
		}
	}

	cfg := Config{
		BindAddress:      net.JoinHostPort(bindHost, strconv.Itoa(port)),
		SecretFile:       secretFile,
		HeadHealthURL:    fmt.Sprintf("http://127.0.0.1:%d/v1/health", headPort),
		ComposeFile:      composeFile,
		ComposeProject:   env("LAUNCHER_COMPOSE_PROJECT", "discordcombatai"),
		StatePath:        statePath,
		LockPath:         lockPath,
		LogPath:          logPath,
		GHCRNamespace:    strings.TrimSuffix(strings.TrimSpace(os.Getenv("LAUNCHER_GHCR_NAMESPACE")), "/"),
		GHCRToken:        os.Getenv("LAUNCHER_GHCR_TOKEN"),
		DockerRetry:      dockerRetry,
		DockerRetryMax:   dockerMax,
		HealthInterval:   healthInterval,
		HealthTimeout:    healthTimeout,
		ShutdownTimeout:  10 * time.Second,
		ReplayRetention:  10 * time.Minute,
		RequestRetention: 24 * time.Hour,
		LogMaxBytes:      10 << 20,
		LogBackups:       5,
	}
	return cfg, nil
}

func (c Config) ValidateDaemon() error {
	if c.SecretFile == "" {
		return errors.New("LAUNCHER_IPC_SECRET_FILE is required")
	}
	if _, err := c.LoadSecret(); err != nil {
		return err
	}
	return c.ValidateEngine()
}

func (c Config) ValidateEngine() error {
	if !validComposeProject(c.ComposeProject) {
		return errors.New("LAUNCHER_COMPOSE_PROJECT must start with a lowercase letter or digit and contain only lowercase letters, digits, hyphens, or underscores")
	}
	if c.GHCRNamespace == "" {
		return errors.New("LAUNCHER_GHCR_NAMESPACE is required")
	}
	u, err := url.Parse("https://" + c.GHCRNamespace)
	if err != nil || u.Host != "ghcr.io" || strings.Trim(u.Path, "/") == "" {
		return errors.New("LAUNCHER_GHCR_NAMESPACE must be under ghcr.io")
	}
	if c.GHCRToken == "" {
		return errors.New("LAUNCHER_GHCR_TOKEN is required")
	}
	info, err := os.Stat(c.ComposeFile)
	if err != nil {
		return fmt.Errorf("compose file: %w", err)
	}
	if info.IsDir() {
		return errors.New("compose file must not be a directory")
	}
	return nil
}

func (c Config) LoadSecret() ([]byte, error) {
	raw, err := os.ReadFile(c.SecretFile)
	if err != nil {
		return nil, fmt.Errorf("read LAUNCHER_IPC_SECRET_FILE: %w", err)
	}
	if len(raw) < minimumSecretBytes {
		return nil, fmt.Errorf("LAUNCHER_IPC_SECRET_FILE must contain at least %d bytes", minimumSecretBytes)
	}
	return raw, nil
}

func env(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func uintEnv(name string, fallback, min, max int) (int, error) {
	raw := env(name, strconv.Itoa(fallback))
	value, err := strconv.Atoi(raw)
	if err != nil || value < min || value > max {
		return 0, fmt.Errorf("%s must be an integer from %d to %d", name, min, max)
	}
	return value, nil
}

func durationEnv(name string, fallback int, allowZero bool) (time.Duration, error) {
	min := 1
	if allowZero {
		min = 0
	}
	value, err := uintEnv(name, fallback, min, 86400)
	if err != nil {
		return 0, err
	}
	return time.Duration(value) * time.Second, nil
}

func absolute(path string) (string, error) {
	if filepath.IsAbs(path) {
		return filepath.Clean(path), nil
	}
	executable, err := os.Executable()
	if err != nil {
		return "", err
	}
	return filepath.Abs(filepath.Join(filepath.Dir(executable), path))
}

func validComposeProject(project string) bool {
	if project == "" || !lowerAlphaNumeric(project[0]) {
		return false
	}
	for index := 1; index < len(project); index++ {
		if !lowerAlphaNumeric(project[index]) && project[index] != '-' && project[index] != '_' {
			return false
		}
	}
	return true
}

func lowerAlphaNumeric(value byte) bool {
	return value >= 'a' && value <= 'z' || value >= '0' && value <= '9'
}
