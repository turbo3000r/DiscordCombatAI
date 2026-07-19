package version

import "testing"

func TestValidate(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name            string
		value           string
		allowPrerelease bool
		valid           bool
	}{
		{"stable", "v1.2.3", false, true},
		{"zero", "v0.0.0", false, true},
		{"manual prerelease", "v1.2.3-rc.1", true, true},
		{"automatic prerelease", "v1.2.3-rc.1", false, false},
		{"build metadata", "v1.2.3+build", true, false},
		{"leading zero", "v01.2.3", false, false},
		{"numeric prerelease leading zero", "v1.2.3-01", true, false},
		{"shell fragment", "v1.2.3;docker ps", true, false},
		{"image reference", "ghcr.io/a/head:v1.2.3", true, false},
		{"missing prefix", "1.2.3", false, false},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			err := Validate(test.value, test.allowPrerelease)
			if (err == nil) != test.valid {
				t.Fatalf("Validate(%q, %t) error = %v", test.value, test.allowPrerelease, err)
			}
		})
	}
}
