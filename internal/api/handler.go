package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
)

var sandboxIDPattern = regexp.MustCompile("^[a-z0-9-]{1,63}$")

type execRequest struct {
	Command []string `json:"cmd"`
}

type execResponse struct {
	ExitCode        int    `json:"exitCode"`
	Stdout          string `json:"stdout"`
	Stderr          string `json:"stderr"`
	OutputTruncated bool   `json:"outputTruncated"`
	Error           string `json:"error,omitempty"`
}

func (s *Server) createSandbox(response http.ResponseWriter, request *http.Request) {
	ctx, cancel := context.WithTimeout(request.Context(), s.createTimeout)
	defer cancel()

	created, err := s.pool.Claim(ctx)
	if err != nil {
		writeJSON(response, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(response, http.StatusCreated, created)
}

func (s *Server) listSandboxes(response http.ResponseWriter, _ *http.Request) {
	items, err := s.manager.List()
	if err != nil {
		writeJSON(response, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(response, http.StatusOK, items)
}

func (s *Server) deleteSandbox(response http.ResponseWriter, request *http.Request) {
	id := request.PathValue("id")
	if !sandboxIDPattern.MatchString(id) {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid sandbox id"})
		return
	}
	if err := s.pool.Release(request.Context(), id); err != nil {
		writeJSON(response, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	response.WriteHeader(http.StatusNoContent)
}

func (s *Server) execSandbox(response http.ResponseWriter, request *http.Request) {
	id := request.PathValue("id")
	if !sandboxIDPattern.MatchString(id) {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid sandbox id"})
		return
	}

	request.Body = http.MaxBytesReader(response, request.Body, maxRequestBodyBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	var input execRequest
	if err := decoder.Decode(&input); err != nil || len(input.Command) == 0 {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "body must contain non-empty cmd array"})
		return
	}

	podName, err := s.manager.PodName(id)
	if err != nil {
		writeJSON(response, http.StatusNotFound, map[string]string{"error": err.Error()})
		return
	}

	stdout := newCappedBuffer(64 << 10)
	stderr := newCappedBuffer(64 << 10)
	ctx, cancel := context.WithTimeout(request.Context(), s.execTimeout)
	defer cancel()
	exitCode, execErr := s.manager.Exec(ctx, podName, input.Command, nil, stdout, stderr)

	result := execResponse{
		ExitCode:        exitCode,
		Stdout:          stdout.String(),
		Stderr:          stderr.String(),
		OutputTruncated: stdout.truncated || stderr.truncated,
	}
	if execErr != nil {
		result.Error = execErr.Error()
		if errors.Is(execErr, context.DeadlineExceeded) {
			writeJSON(response, http.StatusGatewayTimeout, result)
			return
		}
		if exitCode < 0 {
			writeJSON(response, http.StatusBadGateway, result)
			return
		}
	}
	writeJSON(response, http.StatusOK, result)
}

type cappedBuffer struct {
	buffer    bytes.Buffer
	remaining int
	truncated bool
}

func newCappedBuffer(limit int) *cappedBuffer {
	return &cappedBuffer{remaining: limit}
}

func (b *cappedBuffer) Write(value []byte) (int, error) {
	originalLength := len(value)
	if len(value) > b.remaining {
		value = value[:b.remaining]
		b.truncated = true
	}
	if len(value) > 0 {
		_, _ = b.buffer.Write(value)
		b.remaining -= len(value)
	}
	return originalLength, nil
}

func (b *cappedBuffer) String() string {
	return b.buffer.String()
}
