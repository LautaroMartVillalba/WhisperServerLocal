// Package validator provides file validation utilities.
package validator

import (
	"os"
	"path/filepath"
	"strings"
)

// SupportedAudioFormats lists all supported audio extensions.
var SupportedAudioFormats = []string{
	".opus", ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma",
}

// FileExists checks if a file exists at the given path.
func FileExists(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	return !info.IsDir()
}

// ValidateAudioExtension checks if the file has a supported audio extension.
func ValidateAudioExtension(path string) bool {
	ext := strings.ToLower(filepath.Ext(path))
	
	for _, validExt := range SupportedAudioFormats {
		if ext == validExt {
			return true
		}
	}
	return false
}

// GetFileSize returns the size of a file in bytes.
func GetFileSize(path string) (int64, error) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, err
	}
	return info.Size(), nil
}
