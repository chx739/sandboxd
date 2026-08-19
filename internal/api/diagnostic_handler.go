package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/chx739/sandboxd/internal/diagnostic"
)

type diagnosticResponse struct {
	Operation       string `json:"operation"`
	ExitCode        int    `json:"exitCode"`
	Stdout          string `json:"stdout"`
	Stderr          string `json:"stderr"`
	OutputTruncated bool   `json:"outputTruncated"`
	Error           string `json:"error,omitempty"`
}

func (s *Server) kubernetesDiagnostic(response http.ResponseWriter, request *http.Request) {
	id := request.PathValue("id")
	if !sandboxIDPattern.MatchString(id) {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid sandbox id"})
		return
	}

	request.Body = http.MaxBytesReader(response, request.Body, maxRequestBodyBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()

	var input diagnostic.KubernetesRequest
	if err := decoder.Decode(&input); err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]string{
			"error":     "body 必须是合法的 Kubernetes 诊断 JSON",
			"denyLayer": "tool-policy",
		})
		return
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeJSON(response, http.StatusBadRequest, map[string]string{
			"error":     "body 只能包含一个 JSON 对象",
			"denyLayer": "tool-policy",
		})
		return
	}

	command, err := diagnostic.BuildKubernetesCommand(input, s.diagnosticNamespace)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, diagnostic.ErrOperationDenied) || errors.Is(err, diagnostic.ErrNamespaceDenied) {
			status = http.StatusForbidden
		}
		writeJSON(response, status, map[string]string{
			"error":     err.Error(),
			"denyLayer": "tool-policy",
		})
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

	exitCode, execErr := s.manager.Exec(ctx, podName, command, nil, stdout, stderr)
	result := diagnosticResponse{
		Operation:       input.Operation,
		ExitCode:        exitCode,
		Stdout:          stdout.String(),
		Stderr:          stderr.String(),
		OutputTruncated: stdout.truncated || stderr.truncated,
	}
	if execErr == nil && input.Operation == diagnostic.OperationListPods {
		summary, summarizeErr := diagnostic.SummarizePodList(result.Stdout)
		if summarizeErr != nil {
			result.Error = summarizeErr.Error()
			writeJSON(response, http.StatusBadGateway, result)
			return
		}
		result.Stdout = summary
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
