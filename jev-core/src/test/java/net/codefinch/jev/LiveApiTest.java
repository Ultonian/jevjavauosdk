package net.codefinch.jev;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import net.codefinch.jev.exception.JevApiException;
import net.codefinch.jev.exception.JevAuthenticationException;
import net.codefinch.jev.model.ChoiceCriteria;
import net.codefinch.jev.model.Content;
import net.codefinch.jev.model.ModelList;
import net.codefinch.jev.model.NoulCriteria;
import net.codefinch.jev.model.NoulQuestion;
import net.codefinch.jev.model.Questions;
import net.codefinch.jev.model.State;
import net.codefinch.jev.model.SystemOneResponse;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

/**
 * Opt-in probes against the real API: {@code JEV_RUN_LIVE_TESTS=1} and {@code TYPESAFE_API_KEY}.
 * First run 21 Sep 2026, re-run 26 Oct 2026 against jev-1.13.0 and jev-1.14.0 (set {@code
 * TYPESAFE_DEFAULT_MODEL}); the observations are recorded in docs/PARITY.md and asserted here.
 */
@EnabledIfEnvironmentVariable(named = "JEV_RUN_LIVE_TESTS", matches = "1")
class LiveApiTest {

  private static String print(String probe, Object outcome) {
    String line = "[live] " + probe + " -> " + outcome;
    System.out.println(line);
    return line;
  }

  @Test
  void modelsAndOneTinyCall() {
    try (JevClient c = JevClient.fromEnv()) {
      ModelList models = c.models();
      assertThat(models.models()).isNotEmpty();
      print("GET /v1/models", models.models());
      SystemOneResponse r =
          c.systemOne(
              State.of("The invoice was charged twice; please refund one."),
              Questions.of("refund", NoulQuestion.of("Does the text ask for money back?")));
      print("systemone model", r.model() + " usage=" + r.usage());
      assertThat(r.model()).matches("jev-\\d+\\.\\d+\\.\\d+");
      assertThat(r.answers().noul("refund").noul()).isBetween(0.0, 1.0);
    }
  }

  @Test
  void probeUndocumentedInSchemaLimits() {
    try (JevClient c = JevClient.fromEnv()) {
      List<String> eleven = new ArrayList<>();
      for (int i = 0; i < 11; i++) {
        eleven.add("level " + i);
      }
      String eleven11 =
          print(
              "11-level score",
              outcome(
                  () ->
                      c.systemOne(
                          State.of("x"),
                          Questions.builder().score("s", "How much?", eleven).build())));
      // 21 Sep 2026: HTTP 400. 26 Oct 2026: the schema declares maxItems 10 and rejects with 422.
      assertThat(eleven11).contains("rejected: 422").contains("at most 10");
      ChoiceCriteria.Builder many = ChoiceCriteria.builder();
      for (int i = 0; i < 256; i++) {
        many.option("opt" + i);
      }
      String many256 =
          print(
              "256-option choice",
              outcome(
                  () ->
                      c.systemOne(
                          State.of("x"),
                          Questions.builder().choice("c", "Which?", many.build()).build())));
      assertThat(many256).contains("rejected: 422").contains("at most 255");
      SystemOneResponse omitted =
          c.systemOne(
              State.of("Hello there"),
              Questions.of(
                  "n",
                  new NoulQuestion(
                      Optional.empty(),
                      Optional.of(NoulCriteria.of("a greeting", "not a greeting")))));
      SystemOneResponse explicitNull =
          c.systemOne(
              State.of("Hello there"),
              Questions.of(
                  "n",
                  new NoulQuestion(
                      Optional.of(Content.NULL),
                      Optional.of(NoulCriteria.of("a greeting", "not a greeting")))));
      double a = omitted.answers().noul("n").noul();
      double b = explicitNull.answers().noul("n").noul();
      print("instructions omitted vs null", a + " vs " + b);
      assertThat(Math.abs(a - b))
          .as("omitted and null instructions are equivalent")
          .isLessThan(0.1);
    }
  }

  @Test
  void probeInvalidKeyStatus() {
    try (JevClient c = JevClient.builder().apiKey("invalid-key-for-probe").build()) {
      assertThatThrownBy(c::models)
          .isInstanceOf(JevAuthenticationException.class) // observed: an invalid key is 401
          .satisfies(
              e -> print("invalid key", ((JevApiException) e).status() + " " + e.getMessage()));
    }
  }

  private static String outcome(java.util.function.Supplier<SystemOneResponse> call) {
    try {
      SystemOneResponse r = call.get();
      return "accepted: " + r.answers().asMap().keySet() + " " + Map.of("model", r.model());
    } catch (JevApiException e) {
      return "rejected: " + e.status() + " " + e.getMessage();
    }
  }
}
