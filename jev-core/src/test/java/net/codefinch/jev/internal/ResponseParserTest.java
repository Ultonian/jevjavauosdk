package net.codefinch.jev.internal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.Map;
import net.codefinch.jev.Fixtures;
import net.codefinch.jev.exception.JevMissingAnswerException;
import net.codefinch.jev.exception.JevResponseValidationException;
import net.codefinch.jev.model.Answer;
import net.codefinch.jev.model.ChoiceAnswer;
import net.codefinch.jev.model.Content;
import net.codefinch.jev.model.ModelList;
import net.codefinch.jev.model.NoulAnswer;
import net.codefinch.jev.model.ScoreAnswer;
import net.codefinch.jev.model.SystemOneResponse;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class ResponseParserTest {
  private static final String ENDPOINT = "POST https://api.typesafe.ai/v1/systemone";

  private static SystemOneResponse parse(String body) {
    return ResponseParser.parseSystemOne(200, Fixtures.REQ_ID_HEADERS, body, ENDPOINT);
  }

  @Test
  void parsesAllThreeAnswerKindsFromTheDocsExample() {
    SystemOneResponse r = parse(Fixtures.read("responses/docs-all-three.json"));
    assertThat(r.model()).isEqualTo("jev-1.13.0");
    assertThat(r.usage().inputTokens()).isEqualTo(210);
    assertThat(r.usage().outputTokens()).isEqualTo(31);
    assertThat(r.requestId()).contains("req-123");
    assertThat(r.headers().get("content-type")).containsExactly("application/json");
    assertThat(r.rawBody()).contains("\"jev-1.13.0\"");
    assertThat(r.answers().ids()).containsExactly("refund_requested", "department", "severity");

    assertThat(r.answers().noul("refund_requested").noul()).isEqualTo(0.93);
    ChoiceAnswer dept = r.answers().choice("department");
    assertThat(dept.choice()).isEqualTo("billing");
    assertThat(dept.confidence()).isEqualTo(0.6);
    assertThat(dept.probabilities().keySet()).containsExactly("billing", "support", "other");
    ScoreAnswer sev = r.answers().score("severity");
    assertThat(sev.score()).isEqualTo(1.3);
    assertThat(sev.legend()).containsEntry(1, Content.of("Degraded, workaround"));
    assertThat(sev.probabilities()).containsEntry(2, 0.3);
    assertThat(sev.topLevel()).isEqualTo(2);

    // Exhaustive switch over the sealed hierarchy compiles without a default branch.
    for (Answer a : r.answers().asMap().values()) {
      String type =
          switch (a) {
            case NoulAnswer n -> n.type();
            case ChoiceAnswer c -> c.type();
            case ScoreAnswer s -> s.type();
          };
      assertThat(type).isIn("noul", "choice", "score");
    }
  }

  /** typesafe-sdk-python tests/test_responses.py: unknown fields are ignored at every level. */
  @Test
  void unknownFieldsAreIgnored() {
    SystemOneResponse r = parse(Fixtures.read("responses/python-unknown-fields.json"));
    assertThat(r.answers().noul("spam").noul()).isEqualTo(0.9);
    assertThat(r.usage()).isEqualTo(new net.codefinch.jev.model.Usage(1, 1));
  }

  /** typesafe-sdk-python tests/test_responses.py: unknown answer types are dropped, not fatal. */
  @Test
  void unknownAnswerTypeIsDroppedButStaysInRawBody() {
    SystemOneResponse r = parse(Fixtures.read("responses/python-unknown-answer-type.json"));
    assertThat(r.answers().ids()).containsExactly("spam");
    assertThat(r.answers().get("mystery")).isEmpty();
    assertThatThrownBy(() -> r.answers().noul("mystery"))
        .isInstanceOf(JevMissingAnswerException.class)
        .hasMessageContaining("mystery");
    assertThat(r.rawBody()).contains("\"aurora\"");
  }

  @Test
  void structuredLegendContentIsKept() {
    ScoreAnswer s =
        parse(Fixtures.read("responses/python-structured-legend.json")).answers().score("s");
    assertThat(s.legend().get(0)).isInstanceOf(Content.JsonObject.class);
    assertThat(s.legend().get(0).toJson().get("examples").get(1).get("note").isNull()).isTrue();
    assertThat(s.legend().get(1)).isInstanceOf(Content.JsonArray.class);
  }

  /** Python's public model accepts usage {}; the API schema does not. Java follows the schema. */
  @Test
  void emptyUsageIsRejectedAsDocumentedDivergence() {
    assertThatThrownBy(() -> parse(Fixtures.read("responses/usage-empty.json")))
        .isInstanceOf(JevResponseValidationException.class)
        .satisfies(
            e ->
                assertThat(((JevResponseValidationException) e).fieldPath())
                    .isEqualTo("usage.input_tokens"));
  }

  /** typesafe-sdk-python tests/test_responses.py "malformed response raises validation error". */
  @ParameterizedTest(name = "{1}")
  @CsvSource(
      delimiter = '|',
      value = {
        "{\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{}}|model",
        "{\"n\":{\"type\":\"noul\"}}|answers.n.noul",
        "{\"n\":{\"type\":\"noul\",\"noul\":\"0.5\"}}|answers.n.noul",
        "{\"c\":{\"type\":\"choice\",\"choice\":\"a\",\"probabilities\":{}}}|answers.c.confidence",
        "{\"c\":{\"type\":\"choice\",\"confidence\":0.5,\"probabilities\":{}}}|answers.c.choice",
        "{\"s\":{\"type\":\"score\",\"score\":1.0,\"confidence\":1.0,\"legend\":[],\"probabilities\":{}}}|answers.s.legend",
        "{\"s\":{\"type\":\"score\",\"score\":1.0,\"confidence\":1.0,\"legend\":{\"x\":\"bad\"},\"probabilities\":{}}}|answers.s.legend.x",
        "{\"c\":\"not-a-mapping\"}|answers.c.type",
        "{\"c\":{\"noul\":0.5}}|answers.c.type",
        "{\"c\":{\"type\":7}}|answers.c.type",
        "{\"s\":{\"type\":\"score\",\"score\":1.0,\"confidence\":1.0,\"legend\":{\"0\":null},\"probabilities\":{}}}|answers.s.legend.0",
        "{\"s\":{\"type\":\"score\",\"score\":1.0,\"confidence\":1.0,\"legend\":{\"-1\":\"x\"},\"probabilities\":{}}}|answers.s.legend.-1",
        "{\"s\":{\"type\":\"score\",\"score\":1.0,\"confidence\":1.0,\"legend\":{\"0\":\"x\"},\"probabilities\":{\"0\":\"1\"}}}|answers.s.probabilities.0",
        "{\"s\":{\"type\":\"score\",\"score\":1.0,\"confidence\":1.0,\"legend\":{\"0\":\"x\"},\"probabilities\":{\"a\":1}}}|answers.s.probabilities.a",
        "{\"c\":{\"type\":\"choice\",\"choice\":\"a\",\"confidence\":1,\"probabilities\":{\"a\":\"1\"}}}|answers.c.probabilities.a",
        "{\"c\":{\"type\":\"choice\",\"choice\":\"a\",\"confidence\":1,\"probabilities\":[]}}|answers.c.probabilities",
      })
  void malformedResponsesNameTheField(String answersOrBody, String fieldPath) {
    String body =
        fieldPath.equals("model")
            ? answersOrBody
            : "{\"model\":\"test\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":"
                + answersOrBody
                + "}";
    assertThatThrownBy(() -> parse(body))
        .isInstanceOf(JevResponseValidationException.class)
        .satisfies(
            e -> {
              JevResponseValidationException v = (JevResponseValidationException) e;
              assertThat(v.fieldPath()).isEqualTo(fieldPath);
              assertThat(v.status()).isEqualTo(200);
              assertThat(v.requestId()).contains("req-123");
              assertThat(v.rawBody()).isEqualTo(body);
              assertThat(v.endpoint()).contains(ENDPOINT);
            })
        .hasMessage(
            ENDPOINT + ": 200 Invalid response data at '" + fieldPath + "'. (request_id=req-123)");
  }

  @Test
  void missingNumbersNeverBecomeZero() {
    String body =
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{\"c\":{\"type\":\"choice\",\"choice\":\"a\",\"probabilities\":{\"a\":1}}}}";
    assertThatThrownBy(() -> parse(body))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessageContaining("answers.c.confidence");
    String usage =
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1.5,\"output_tokens\":1},"
            + "\"answers\":{\"n\":{\"type\":\"noul\",\"noul\":0.5}}}";
    assertThatThrownBy(() -> parse(usage)).hasMessageContaining("usage.input_tokens");
  }

  @ParameterizedTest
  @CsvSource(
      delimiter = '|',
      value = {"not json|$", "[1]|$", "\"text\"|$", "|$", "{\"model\":\"m\"}|answers"})
  void bodyMustBeJsonObject(String body, String fieldPath) {
    assertThatThrownBy(() -> parse(body == null ? "" : body))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessageContaining("'" + fieldPath + "'");
  }

  /** Numeric conversion must not silently wrap or overflow. */
  @ParameterizedTest(name = "{1}")
  @CsvSource(
      delimiter = '|',
      value = {
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":9223372036854775808,\"output_tokens\":1},\"answers\":{\"n\":{\"type\":\"noul\",\"noul\":0.5}}}|usage.input_tokens",
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":-9223372036854775809},\"answers\":{\"n\":{\"type\":\"noul\",\"noul\":0.5}}}|usage.output_tokens",
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{\"n\":{\"type\":\"noul\",\"noul\":1e309}}}|answers.n.noul",
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{\"c\":{\"type\":\"choice\",\"choice\":\"a\",\"confidence\":-1e999,\"probabilities\":{\"a\":1}}}}|answers.c.confidence",
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{\"c\":{\"type\":\"choice\",\"choice\":\"a\",\"confidence\":1,\"probabilities\":{\"a\":1e400}}}}|answers.c.probabilities.a",
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{\"s\":{\"type\":\"score\",\"score\":1e400,\"confidence\":1,\"legend\":{\"0\":\"x\"},\"probabilities\":{\"0\":1}}}}|answers.s.score",
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{\"s\":{\"type\":\"score\",\"score\":1,\"confidence\":1,\"legend\":{\"0\":\"x\"},\"probabilities\":{\"0\":1e400}}}}|answers.s.probabilities.0",
      })
  void unrepresentableNumbersAreRejectedNotWrapped(String body, String fieldPath) {
    assertThatThrownBy(() -> parse(body))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessageContaining("'" + fieldPath + "'");
  }

  @Test
  void numericBoundariesThatFitAreAccepted() {
    SystemOneResponse r =
        parse(
            "{\"model\":\"m\",\"usage\":{\"input_tokens\":9223372036854775807,\"output_tokens\":0},"
                + "\"answers\":{\"n\":{\"type\":\"noul\",\"noul\":1.7976931348623157e308}}}");
    assertThat(r.usage().inputTokens()).isEqualTo(Long.MAX_VALUE);
    assertThat(r.usage().outputTokens()).isZero();
    assertThat(r.answers().noul("n").noul()).isEqualTo(Double.MAX_VALUE);
  }

  /** Response parsing rejects trailing content after the JSON document. */
  @ParameterizedTest
  @CsvSource(
      delimiter = '|',
      value = {" invalid trailing content", " {}", "}", "[]"})
  void trailingContentAfterValidDocumentIsRejectedOnBothEndpoints(String trailing) {
    String systemOne = Fixtures.read("responses/docs-all-three.json").trim() + trailing;
    assertThatThrownBy(() -> parse(systemOne))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessageContaining("'$'");
    String models = Fixtures.read("responses/models.json").trim() + trailing;
    assertThatThrownBy(() -> ResponseParser.parseModels(200, Map.of(), models, null))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessageContaining("'$'");
  }

  /**
   * Jev 1.14 (OpenAPI snapshot 2026-10-26) adds {@code box} answers; 0.1.0 treats them as unknown.
   */
  @Test
  void boxAnswerIsDroppedButStaysInRawBody() {
    SystemOneResponse r = parse(Fixtures.read("responses/jev114-box-answer.json"));
    assertThat(r.answers().ids()).containsExactly("spam");
    assertThat(r.answers().get("logo")).isEmpty();
    assertThat(r.rawBody()).contains("\"box\"").contains("\"confidence\"");
  }

  /** The schema requires at least one entry in the answers object. */
  @Test
  void emptyWireAnswersAreRejectedButAllUnknownTypesYieldAnEmptyTypedMap() {
    String empty =
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},\"answers\":{}}";
    assertThatThrownBy(() -> parse(empty))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessageContaining("'answers'");
    String onlyUnknown =
        "{\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1},"
            + "\"answers\":{\"x\":{\"type\":\"aurora\",\"value\":3}}}";
    SystemOneResponse r = parse(onlyUnknown);
    assertThat(r.answers().size()).isZero();
    assertThat(r.rawBody()).contains("aurora");
  }

  @Test
  void parsesModelsAndIgnoresExtraFields() {
    ModelList list =
        ResponseParser.parseModels(200, Map.of(), Fixtures.read("responses/models.json"), "GET x");
    assertThat(list.models()).hasSize(2);
    assertThat(list.models().get(0).name()).isEqualTo("jev-latest");
    assertThat(list.models().get(1).releaseDate()).isEqualTo("2026-09-15");
    assertThat(list.requestId()).isEmpty();
  }

  @ParameterizedTest
  @CsvSource(
      delimiter = '|',
      value = {
        "{}|models",
        "{\"models\":null}|models",
        "{\"models\":\"bad\"}|models",
        "{\"models\":[1]}|models.0",
        "{\"models\":[{\"name\":\"a\",\"description\":\"d\"}]}|models.0.release_date",
        "{\"models\":[{\"name\":\"a\",\"release_date\":\"d\"}]}|models.0.description",
        "{\"models\":[{\"description\":\"a\",\"release_date\":\"d\"}]}|models.0.name",
      })
  void malformedModelsNameTheField(String body, String fieldPath) {
    assertThatThrownBy(() -> ResponseParser.parseModels(200, Map.of(), body, null))
        .isInstanceOf(JevResponseValidationException.class)
        .hasMessage("200 Invalid response data at '" + fieldPath + "'.");
  }

  @Test
  void answersAreUnmodifiableAndOrdered() {
    SystemOneResponse r = parse(Fixtures.read("responses/docs-all-three.json"));
    assertThatThrownBy(() -> r.answers().asMap().clear())
        .isInstanceOf(UnsupportedOperationException.class);
    assertThatThrownBy(() -> r.answers().choice("department").probabilities().clear())
        .isInstanceOf(UnsupportedOperationException.class);
    assertThatThrownBy(() -> r.answers().score("severity").legend().clear())
        .isInstanceOf(UnsupportedOperationException.class);
    assertThatThrownBy(() -> r.headers().clear()).isInstanceOf(UnsupportedOperationException.class);
    assertThat(List.copyOf(r.answers().score("severity").probabilities().keySet()))
        .containsExactly(0, 1, 2);
  }
}
