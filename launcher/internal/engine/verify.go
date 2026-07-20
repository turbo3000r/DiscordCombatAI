package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/auth"
)

type Verifier interface {
	Verify(context.Context, string) error
}

type HeadVerifier struct {
	url      string
	interval time.Duration
	client   *http.Client
	auth     *auth.Authenticator
	now      func() time.Time
}

func NewHeadVerifier(url string, interval time.Duration, authenticator *auth.Authenticator) *HeadVerifier {
	transport := &http.Transport{
		DialContext: (&net.Dialer{Timeout: 2 * time.Second}).DialContext,
	}
	return &HeadVerifier{
		url: url, interval: interval, auth: authenticator,
		client: &http.Client{Transport: transport, Timeout: 5 * time.Second},
		now:    func() time.Time { return time.Now().UTC() },
	}
}

func (v *HeadVerifier) Verify(ctx context.Context, target string) error {
	var lastError error
	for {
		if err := v.poll(ctx, target); err == nil {
			return nil
		} else {
			lastError = err
		}
		timer := time.NewTimer(v.interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			if lastError == nil {
				lastError = ctx.Err()
			}
			return fmt.Errorf("Head verification exhausted: %w", lastError)
		case <-timer.C:
		}
	}
}

func (v *HeadVerifier) poll(ctx context.Context, target string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, v.url, nil)
	if err != nil {
		return err
	}
	requestID, err := auth.NewRequestID()
	if err != nil {
		return err
	}
	if err := v.auth.SignRequest(request, nil, requestID, v.now()); err != nil {
		return err
	}
	response, err := v.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 16*1024+1))
	if err != nil {
		return err
	}
	if len(body) > 16*1024 {
		return fmt.Errorf("Head health response exceeds 16 KiB")
	}
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("Head health returned HTTP %d", response.StatusCode)
	}
	var health struct {
		SchemaVersion int    `json:"schema_version"`
		Status        string `json:"status"`
		Version       string `json:"version"`
		InstanceID    string `json:"instance_id"`
		StartedAt     string `json:"started_at"`
	}
	if err := json.Unmarshal(body, &health); err != nil {
		return fmt.Errorf("invalid Head health response: %w", err)
	}
	if health.SchemaVersion != 1 || health.Status != "alive" || health.Version != target {
		return fmt.Errorf("Head health does not report alive at exact target %s", target)
	}
	if _, err := auth.NormalizeUUID(health.InstanceID); err != nil {
		return fmt.Errorf("invalid Head instance_id")
	}
	startedAt, err := time.Parse(time.RFC3339, health.StartedAt)
	if err != nil || startedAt.IsZero() {
		return fmt.Errorf("invalid Head started_at")
	}
	return nil
}
