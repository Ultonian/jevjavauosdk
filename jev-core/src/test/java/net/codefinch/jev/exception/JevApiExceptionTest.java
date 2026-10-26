package net.codefinch.jev.exception;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class JevApiExceptionTest {
  private static final Map<String, List<String>> NO_HEADERS = Map.of();

  @ParameterizedTest
  @CsvSource({
    "400, JevBadRequestException",
    "401, JevAuthenticationException",
    "403, JevPermissionDeniedException",
    "404, JevNotFoundException",
    "422, JevUnprocessableEntityException",
    "429, JevRateLimitException",
    "500, JevInternalServerException",
    "529, JevInternalServerException",
    "599, JevInternalServerException",
    "418, JevApiException",
    "302, JevApiException",
  })
  void statusMapsToTheUpstreamClass(int status, String className) {
    JevApiException e = JevApiException.fromStatus(status, NO_HEADERS, "", null);
    assertThat(e.getClass().getSimpleName()).isEqualTo(className);
    assertThat(e.status()).isEqualTo(status);
    assertThat(e).isInstanceOf(JevException.class).isInstanceOf(RuntimeException.class);
  }

  /**
   * Message cases from typesafe-sdk-python tests/test_errors.py and typesafe-sdk-js
   * test/errors.test.ts.
   */
  @ParameterizedTest
  @CsvSource(
      delimiter = '|',
      value = {
        "400|{\"message\":\"Bad request\"}|400 Bad request",
        "401|{\"error\":{\"message\":\"invalid api key\"}}|401 invalid api key",
        "400|{\"error\":\"plain\"}|400 plain",
        "400|{\"detail\":\"as string\"}|400 as string",
        "400|{\"detail\":{\"message\":\"nested\"}}|400 nested",
        // HTTPBadRequestError, documented in the OpenAPI snapshot of 2026-10-26.
        "400|{\"detail\":{\"error_type\":\"invalid_request\",\"message\":\"Too many choice"
            + " options\"}}|400 Too many choice options",
        "400|{\"code\":7}|400 {\"code\":7}",
        "400|{\"error\":\"\",\"message\":\"ignored\"}|400 {\"error\":\"\",\"message\":\"ignored\"}",
        "400|{\"detail\":[null,42,{\"msg\":4}]}|400 {\"detail\":[null,42,{\"msg\":4}]}",
        "502|<h1>bad gateway</h1>|502 <h1>bad gateway</h1>",
        "429||429 status code (no body)",
        "400|\"a json string\"|400 a json string",
        "400|[1,2]|400 [1,2]",
        "422|{\"detail\":[{\"loc\":[\"body\",\"questions\",\"q\",\"criteria\"],\"msg\":\"Field"
            + " required\",\"type\":\"missing\"}]}|422 questions.q.criteria: Field required",
        "422|{\"detail\":[{\"loc\":\"x\",\"msg\":\"no path\"},{\"msg\":\"second\"}]}|422 no path;"
            + " second",
      })
  void messageIsExtractedInUpstreamOrder(int status, String body, String expected) {
    JevApiException e =
        JevApiException.fromStatus(status, NO_HEADERS, body == null ? "" : body, null);
    assertThat(e.getMessage()).isEqualTo(expected);
    assertThat(e.rawBody()).isEqualTo(body == null ? "" : body);
  }

  @Test
  void longFallbackIsTruncatedTo200Chars() {
    String body = "{\"code\":\"" + "x".repeat(300) + "\"}";
    JevApiException e = JevApiException.fromStatus(400, NO_HEADERS, body, null);
    assertThat(e.getMessage()).hasSize("400 ".length() + 200 + 1).endsWith("…");
    assertThat(e.rawBody()).isEqualTo(body);
  }

  @Test
  void endpointAndRequestIdFrameTheMessage() {
    Map<String, List<String>> headers = Map.of("X-TypeSafe-Request-Id", List.of("req-worker"));
    JevApiException e =
        JevApiException.fromStatus(
            400,
            headers,
            "{\"message\":\"Bad request\"}",
            "POST https://example.test/v1/systemone");
    assertThat(e.getMessage())
        .isEqualTo(
            "POST https://example.test/v1/systemone: 400 Bad request (request_id=req-worker)");
    assertThat(e.requestId()).contains("req-worker");
    assertThat(e.endpoint()).contains("POST https://example.test/v1/systemone");
    assertThat(e.headers().get("x-typesafe-request-id")).containsExactly("req-worker");
    assertThatThrownBy(() -> e.headers().clear()).isInstanceOf(UnsupportedOperationException.class);
    assertThat(JevApiException.fromStatus(400, null, "", null).headers()).isEmpty();
  }

  @Test
  void unprocessableEntityExposesFieldErrorsOnlyForFastApiShape() {
    String fastApi =
        "{\"detail\":[{\"loc\":[\"body\",\"questions\",\"q\",\"criteria\"],\"msg\":\"Field"
            + " required\",\"type\":\"missing\"},"
            + "{\"loc\":[\"body\",0],\"msg\":\"bad\"},\"skip\",{\"msg\":5}]}";
    JevUnprocessableEntityException e =
        (JevUnprocessableEntityException)
            JevApiException.fromStatus(422, NO_HEADERS, fastApi, null);
    assertThat(e.fieldErrors())
        .containsExactly(
            new JevUnprocessableEntityException.FieldError(
                "questions.q.criteria", "Field required", "missing"),
            new JevUnprocessableEntityException.FieldError("0", "bad", ""));
    assertThat(
            ((JevUnprocessableEntityException)
                    JevApiException.fromStatus(422, NO_HEADERS, "text", null))
                .fieldErrors())
        .isEmpty();
    assertThat(
            ((JevUnprocessableEntityException)
                    JevApiException.fromStatus(422, NO_HEADERS, "{\"detail\":\"s\"}", null))
                .fieldErrors())
        .isEmpty();
  }

  @Test
  void rateLimitExposesRetryAfterAndServerErrorKnowsOverloaded() {
    JevRateLimitException limited =
        (JevRateLimitException)
            JevApiException.fromStatus(429, Map.of("Retry-After-Ms", List.of("1500")), "", null);
    assertThat(limited.retryAfter()).contains(Duration.ofMillis(1500));
    assertThat(
            ((JevRateLimitException) JevApiException.fromStatus(429, NO_HEADERS, "", null))
                .retryAfter())
        .isEmpty();
    assertThat(
            ((JevRateLimitException) JevApiException.fromStatus(429, null, "", null)).retryAfter())
        .isEmpty();
    JevInternalServerException overloaded =
        (JevInternalServerException) JevApiException.fromStatus(529, NO_HEADERS, "", null);
    assertThat(overloaded.isOverloaded()).isTrue();
    assertThat(
            ((JevInternalServerException) JevApiException.fromStatus(500, NO_HEADERS, "", null))
                .isOverloaded())
        .isFalse();
  }

  @Test
  void transportAndJavaOnlyExceptionsFormHierarchy() {
    Throwable cause = new java.io.IOException("io");
    assertThat(new JevTimeoutException("t", cause)).isInstanceOf(JevConnectionException.class);
    assertThat(new JevDeadlineExceededException("d", cause))
        .isInstanceOf(JevTimeoutException.class);
    assertThat(new JevDeadlineExceededException("d", null).getCause()).isNull();
    assertThat(new JevInterruptedException("i", new InterruptedException()))
        .isInstanceOf(JevException.class);
    assertThat(new JevConnectionException("c", cause).getCause()).isSameAs(cause);
    assertThat(new JevException("plain").getMessage()).isEqualTo("plain");
    assertThat(new JevException("with", cause).getCause()).isSameAs(cause);
  }
}
