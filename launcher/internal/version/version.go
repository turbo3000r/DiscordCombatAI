package version

import (
	"errors"
	"strings"
	"unicode"
)

var ErrInvalid = errors.New("version must be vMAJOR.MINOR.PATCH with an optional SemVer prerelease")

func Validate(value string, allowPrerelease bool) error {
	if value == "" || strings.TrimSpace(value) != value || !strings.HasPrefix(value, "v") {
		return ErrInvalid
	}
	coreAndPre := strings.Split(value[1:], "-")
	if len(coreAndPre) > 2 {
		return ErrInvalid
	}
	core := strings.Split(coreAndPre[0], ".")
	if len(core) != 3 {
		return ErrInvalid
	}
	for _, part := range core {
		if !validNumeric(part) {
			return ErrInvalid
		}
	}
	if len(coreAndPre) == 1 {
		return nil
	}
	if !allowPrerelease || coreAndPre[1] == "" {
		return ErrInvalid
	}
	for _, identifier := range strings.Split(coreAndPre[1], ".") {
		if !validPrereleaseIdentifier(identifier) {
			return ErrInvalid
		}
	}
	return nil
}

func IsPrerelease(value string) bool {
	return strings.Contains(value, "-")
}

func validNumeric(value string) bool {
	if value == "" || (len(value) > 1 && value[0] == '0') {
		return false
	}
	for _, r := range value {
		if !unicode.IsDigit(r) || r > unicode.MaxASCII {
			return false
		}
	}
	return true
}

func validPrereleaseIdentifier(value string) bool {
	if value == "" {
		return false
	}
	numeric := true
	for _, r := range value {
		if !(r >= '0' && r <= '9') {
			numeric = false
		}
		if !((r >= '0' && r <= '9') || (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') || r == '-') {
			return false
		}
	}
	return !numeric || len(value) == 1 || value[0] != '0'
}
