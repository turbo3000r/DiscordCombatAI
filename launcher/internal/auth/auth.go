package auth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	TimestampHeader = "X-DCA-Timestamp"
	RequestIDHeader = "X-DCA-Request-ID"
	SignatureHeader = "X-DCA-Signature"
	MaxClockSkew    = 30 * time.Second
)

var ErrUnauthorized = errors.New("unauthorized")

type ReplayStore interface {
	CheckReplay(requestID, signature, digest, path string, allowIdentical bool) (bool, error)
}

type Authenticator struct {
	secret []byte
	replay ReplayStore
	now    func() time.Time
}

func New(secret []byte, replay ReplayStore) *Authenticator {
	copySecret := append([]byte(nil), secret...)
	return &Authenticator{
		secret: copySecret, replay: replay,
		now: func() time.Time { return time.Now().UTC() },
	}
}

func Canonical(method, path string, timestamp int64, requestID string, body []byte) (string, error) {
	requestID, err := NormalizeUUID(requestID)
	if err != nil {
		return "", err
	}
	hash := sha256.Sum256(body)
	return strings.Join([]string{
		strings.ToUpper(method),
		path,
		strconv.FormatInt(timestamp, 10),
		requestID,
		hex.EncodeToString(hash[:]),
	}, "\n"), nil
}

func Sign(secret []byte, method, path string, timestamp int64, requestID string, body []byte) (string, error) {
	canonical, err := Canonical(method, path, timestamp, requestID, body)
	if err != nil {
		return "", err
	}
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(canonical))
	return hex.EncodeToString(mac.Sum(nil)), nil
}

func (a *Authenticator) Authenticate(request *http.Request, body []byte, allowIdentical bool) (string, error) {
	rawTimestamp := request.Header.Get(TimestampHeader)
	requestID, err := NormalizeUUID(request.Header.Get(RequestIDHeader))
	if err != nil {
		return "", ErrUnauthorized
	}
	timestamp, err := strconv.ParseInt(rawTimestamp, 10, 64)
	if err != nil || strconv.FormatInt(timestamp, 10) != rawTimestamp {
		return "", ErrUnauthorized
	}
	now := a.now().Unix()
	if delta := now - timestamp; delta > int64(MaxClockSkew.Seconds()) || delta < -int64(MaxClockSkew.Seconds()) {
		return "", ErrUnauthorized
	}
	signature := request.Header.Get(SignatureHeader)
	if len(signature) != sha256.Size*2 || signature != strings.ToLower(signature) {
		return "", ErrUnauthorized
	}
	if _, err := hex.DecodeString(signature); err != nil {
		return "", ErrUnauthorized
	}
	expected, err := Sign(a.secret, request.Method, request.URL.RequestURI(), timestamp, requestID, body)
	if err != nil || !hmac.Equal([]byte(expected), []byte(signature)) {
		return "", ErrUnauthorized
	}
	digest := sha256.Sum256(body)
	if a.replay != nil {
		if _, err := a.replay.CheckReplay(requestID, signature, hex.EncodeToString(digest[:]), request.URL.RequestURI(), allowIdentical); err != nil {
			return "", ErrUnauthorized
		}
	}
	return requestID, nil
}

func (a *Authenticator) SignRequest(request *http.Request, body []byte, requestID string, at time.Time) error {
	requestID, err := NormalizeUUID(requestID)
	if err != nil {
		return err
	}
	timestamp := at.UTC().Unix()
	signature, err := Sign(a.secret, request.Method, request.URL.RequestURI(), timestamp, requestID, body)
	if err != nil {
		return err
	}
	request.Header.Set(TimestampHeader, strconv.FormatInt(timestamp, 10))
	request.Header.Set(RequestIDHeader, requestID)
	request.Header.Set(SignatureHeader, signature)
	return nil
}

func NormalizeUUID(value string) (string, error) {
	value = strings.ToLower(value)
	if len(value) != 36 || value[8] != '-' || value[13] != '-' || value[18] != '-' || value[23] != '-' {
		return "", fmt.Errorf("invalid UUID")
	}
	compact := strings.ReplaceAll(value, "-", "")
	if len(compact) != 32 {
		return "", fmt.Errorf("invalid UUID")
	}
	raw, err := hex.DecodeString(compact)
	if err != nil || len(raw) != 16 {
		return "", fmt.Errorf("invalid UUID")
	}
	return value, nil
}

func NewRequestID() (string, error) {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", err
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	raw := hex.EncodeToString(value[:])
	return raw[0:8] + "-" + raw[8:12] + "-" + raw[12:16] + "-" + raw[16:20] + "-" + raw[20:32], nil
}
