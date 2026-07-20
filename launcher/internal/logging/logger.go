package logging

import (
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Logger struct {
	mu       sync.Mutex
	path     string
	maxBytes int64
	backups  int
	secrets  []string
	file     *os.File
	base     *log.Logger
}

func New(path string, maxBytes int64, backups int, secrets ...string) (*Logger, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	l := &Logger{path: path, maxBytes: maxBytes, backups: backups}
	for _, secret := range secrets {
		if secret != "" {
			l.secrets = append(l.secrets, secret)
		}
	}
	if err := l.open(); err != nil {
		return nil, err
	}
	return l, nil
}

func (l *Logger) Close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.file == nil {
		return nil
	}
	return l.file.Close()
}

func (l *Logger) Info(module, format string, args ...any) {
	l.write("INFO", module, format, args...)
}

func (l *Logger) Warning(module, format string, args ...any) {
	l.write("WARNING", module, format, args...)
}

func (l *Logger) Error(module, format string, args ...any) {
	l.write("ERROR", module, format, args...)
}

func (l *Logger) write(level, module, format string, args ...any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	message := fmt.Sprintf(format, args...)
	for _, secret := range l.secrets {
		message = strings.ReplaceAll(message, secret, "[REDACTED]")
	}
	_ = l.rotateIfNeeded(int64(len(message) + 128))
	l.base.Printf("[%s][%s][launcher][%s]: %s", time.Now().UTC().Format(time.RFC3339), level, module, message)
}

func (l *Logger) open() error {
	file, err := os.OpenFile(l.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	l.file = file
	l.base = log.New(io.MultiWriter(os.Stderr, file), "", 0)
	return nil
}

func (l *Logger) rotateIfNeeded(incoming int64) error {
	if l.maxBytes <= 0 {
		return nil
	}
	info, err := l.file.Stat()
	if err != nil || info.Size()+incoming <= l.maxBytes {
		return err
	}
	if err := l.file.Close(); err != nil {
		return err
	}
	for i := l.backups - 1; i >= 1; i-- {
		_ = os.Rename(fmt.Sprintf("%s.%d", l.path, i), fmt.Sprintf("%s.%d", l.path, i+1))
	}
	if l.backups > 0 {
		_ = os.Rename(l.path, l.path+".1")
	} else {
		_ = os.Remove(l.path)
	}
	return l.open()
}
