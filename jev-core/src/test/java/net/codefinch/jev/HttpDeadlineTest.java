package net.codefinch.jev;

import static net.codefinch.jev.HttpTestFixture.OK;
import static net.codefinch.jev.HttpTestFixture.REQUEST;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.ProxySelector;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicLong;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import net.codefinch.jev.exception.JevDeadlineExceededException;
import net.codefinch.jev.exception.JevInternalServerException;
import net.codefinch.jev.exception.JevTimeoutException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class HttpDeadlineTest {
  private HttpTestFixture fixture;
  private TestServer server;
  private List<Duration> sleeps;

  @BeforeEach
  void start() throws IOException {
    fixture = new HttpTestFixture();
    server = fixture.server;
    sleeps = fixture.sleeps;
  }

  @AfterEach
  void stop() {
    fixture.close();
  }

  private JevClientBuilder client() {
    return fixture.client();
  }

  @Test
  void stalledHeadersTimeOutPerAttemptAndAreRetried() {
    server
        .enqueue(TestServer.Scripted.json(200, OK).stallingHeaders(Duration.ofSeconds(5)))
        .enqueueJson(200, OK);
    try (JevClient c = client().timeout(Duration.ofMillis(200)).build()) {
      assertThat(c.systemOne(REQUEST).model()).isEqualTo("jev-1.13.0");
    }
    assertThat(server.requests()).hasSize(2);
  }

  @Test
  void stalledBodyTimesOutToo() {
    CountDownLatch release = new CountDownLatch(1);
    server.enqueue(TestServer.Scripted.json(200, OK).stallingBody(20, release));
    try (JevClient c =
        client().timeout(Duration.ofMillis(300)).retryPolicy(RetryPolicy.NONE).build()) {
      assertThatThrownBy(() -> c.systemOne(REQUEST))
          .isInstanceOf(JevTimeoutException.class)
          .isNotInstanceOf(JevDeadlineExceededException.class)
          .hasMessageContaining("timed out after 300 ms");
    } finally {
      release.countDown();
    }
  }

  /**
   * The JDK request timeout shares the attempt budget and can fire before the SDK's own wait does;
   * both outcomes must report the same attempt timeout.
   */
  @Test
  void jdkRequestTimeoutReportsTheAttemptBudget() {
    HttpTimeoutException jdk = new HttpTimeoutException("request timed out");
    try (JevClient c =
        client()
            .httpClient(new FailingHttpClient(jdk))
            .timeout(Duration.ofMillis(300))
            .retryPolicy(RetryPolicy.NONE)
            .build()) {
      assertThatThrownBy(() -> c.systemOne(REQUEST))
          .isInstanceOf(JevTimeoutException.class)
          .isNotInstanceOf(JevDeadlineExceededException.class)
          .hasMessageContaining("attempt timed out after 300 ms")
          .hasCause(jdk);
    }
  }

  @Test
  void deadlineRefusesRetryThatWouldBreachIt() {
    AtomicLong nanos = new AtomicLong();
    server.enqueueJson(500, "{\"message\":\"first\"}");
    try (JevClient c = client().nanoTime(nanos::get).deadline(Duration.ofMillis(400)).build()) {
      assertThatThrownBy(() -> c.systemOne(REQUEST))
          .isInstanceOf(JevDeadlineExceededException.class)
          .hasMessageContaining("400 ms exceeded after 1 attempt(s)")
          .hasCauseInstanceOf(JevInternalServerException.class);
    }
    assertThat(server.requests()).hasSize(1);
    assertThat(sleeps).isEmpty(); // 500 ms backoff >= 400 ms deadline: fail fast, no sleep
  }

  @Test
  void deadlineExpiringDuringAnActiveAttemptCancelsIt() {
    server.enqueue(TestServer.Scripted.json(200, OK).stallingHeaders(Duration.ofSeconds(5)));
    try (JevClient c =
        client().timeout(Duration.ofSeconds(10)).deadline(Duration.ofMillis(300)).build()) {
      long start = System.nanoTime();
      assertThatThrownBy(() -> c.systemOne(REQUEST))
          .isInstanceOf(JevDeadlineExceededException.class)
          .hasCauseInstanceOf(JevTimeoutException.class);
      assertThat(Duration.ofNanos(System.nanoTime() - start)).isLessThan(Duration.ofSeconds(3));
    }
    assertThat(server.requests()).hasSize(1);
  }

  @Test
  void deadlineCanBeDisabledPerCallAndPerClient() {
    server.enqueueJson(500, "{}").enqueueJson(200, OK).enqueueJson(500, "{}").enqueueJson(200, OK);
    AtomicLong nanos = new AtomicLong();
    try (JevClient c = client().nanoTime(nanos::get).deadline(Duration.ofMillis(100)).build()) {
      assertThat(c.systemOne(REQUEST, RequestOptions.builder().noDeadline().build()).model())
          .isEqualTo("jev-1.13.0");
    }
    try (JevClient c = client().nanoTime(nanos::get).noDeadline().build()) {
      assertThat(c.systemOne(REQUEST).model()).isEqualTo("jev-1.13.0");
    }
    assertThat(server.requests()).hasSize(4);
  }

  /** An injected transport whose every exchange fails the way the JDK reports a request timeout. */
  private static final class FailingHttpClient extends HttpClient {
    private final IOException failure;

    FailingHttpClient(IOException failure) {
      this.failure = failure;
    }

    @Override
    public <T> CompletableFuture<HttpResponse<T>> sendAsync(
        HttpRequest request, HttpResponse.BodyHandler<T> handler) {
      return CompletableFuture.failedFuture(failure);
    }

    @Override
    public <T> CompletableFuture<HttpResponse<T>> sendAsync(
        HttpRequest request,
        HttpResponse.BodyHandler<T> handler,
        HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
      return sendAsync(request, handler);
    }

    @Override
    public <T> HttpResponse<T> send(HttpRequest request, HttpResponse.BodyHandler<T> handler)
        throws IOException {
      throw failure;
    }

    @Override
    public Redirect followRedirects() {
      return Redirect.NEVER;
    }

    @Override
    public Optional<CookieHandler> cookieHandler() {
      return Optional.empty();
    }

    @Override
    public Optional<Duration> connectTimeout() {
      return Optional.empty();
    }

    @Override
    public Optional<ProxySelector> proxy() {
      return Optional.empty();
    }

    @Override
    public SSLContext sslContext() {
      throw new UnsupportedOperationException();
    }

    @Override
    public SSLParameters sslParameters() {
      throw new UnsupportedOperationException();
    }

    @Override
    public Optional<Authenticator> authenticator() {
      return Optional.empty();
    }

    @Override
    public Version version() {
      return Version.HTTP_1_1;
    }

    @Override
    public Optional<Executor> executor() {
      return Optional.empty();
    }
  }
}
