package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/chx739/sandboxd/internal/approval"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
)

func (s *Server) proposePlan(response http.ResponseWriter, request *http.Request) {
	request.Body = http.MaxBytesReader(response, request.Body, maxRequestBodyBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()

	var input approval.ProposeInput
	if err := decoder.Decode(&input); err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "body 必须包含 namespace、name 和 replicas"})
		return
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "body 只能包含一个 JSON 对象"})
		return
	}

	plan, err := s.plans.Propose(request.Context(), input)
	if err != nil {
		writePlanError(response, err)
		return
	}
	writeJSON(response, http.StatusCreated, plan)
}

func (s *Server) listPlans(response http.ResponseWriter, _ *http.Request) {
	writeJSON(response, http.StatusOK, s.plans.List())
}

func (s *Server) approvePlan(response http.ResponseWriter, request *http.Request) {
	id := request.PathValue("id")
	if !sandboxIDPattern.MatchString(id) {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid plan id"})
		return
	}
	plan, err := s.plans.Approve(request.Context(), id)
	if err != nil {
		writePlanError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, plan)
}

func (s *Server) rejectPlan(response http.ResponseWriter, request *http.Request) {
	id := request.PathValue("id")
	if !sandboxIDPattern.MatchString(id) {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid plan id"})
		return
	}
	plan, err := s.plans.Reject(id)
	if err != nil {
		writePlanError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, plan)
}

func writePlanError(response http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	switch {
	case errors.Is(err, approval.ErrNamespaceDenied),
		errors.Is(err, approval.ErrReplicasDenied),
		errors.Is(err, approval.ErrTargetInvalid):
		status = http.StatusBadRequest
	case errors.Is(err, approval.ErrPlanNotFound), apierrors.IsNotFound(err):
		status = http.StatusNotFound
	case errors.Is(err, approval.ErrPlanState), errors.Is(err, approval.ErrTargetChanged):
		status = http.StatusConflict
	case apierrors.IsInvalid(err), apierrors.IsForbidden(err):
		status = http.StatusUnprocessableEntity
	}
	writeJSON(response, status, map[string]string{"error": err.Error()})
}
