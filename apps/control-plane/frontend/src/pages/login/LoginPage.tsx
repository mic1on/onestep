import { FormEvent, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useConsoleSessionQuery } from "../../features/auth/queries";
import { loginConsole } from "../../lib/api/client";
import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";

function sanitizeNextPath(candidate: string | null) {
  if (!candidate || !candidate.startsWith("/") || candidate.startsWith("//")) {
    return "/services?environment=prod";
  }
  return candidate;
}

export function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const sessionQuery = useConsoleSessionQuery();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const nextPath = sanitizeNextPath(searchParams.get("next"));

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const session = await loginConsole(username, password);
      queryClient.setQueryData(["console-session"], session);
      navigate(nextPath, { replace: true });
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  if (sessionQuery.isPending) {
    return (
      <div className="auth-shell">
        <Panel title={t("auth.checkingTitle")} subtitle={t("auth.loginSubtitle")} className="auth-panel">
          <div className="loading-block">{t("auth.checkingBody")}</div>
        </Panel>
      </div>
    );
  }

  if (sessionQuery.error) {
    return (
      <div className="auth-shell">
        <Panel title={t("auth.loginTitle")} subtitle={t("auth.loginSubtitle")} className="auth-panel">
          <EmptyState title={t("auth.checkFailedTitle")} body={String(sessionQuery.error)} />
        </Panel>
      </div>
    );
  }

  if (!sessionQuery.data?.auth_configured || sessionQuery.data.authenticated) {
    return <Navigate to={nextPath} replace />;
  }

  return (
    <div className="auth-shell">
      <Panel title={t("auth.loginTitle")} subtitle={t("auth.loginSubtitle")} className="auth-panel">
        <form className="auth-form" onSubmit={(event) => void handleSubmit(event)}>
          <label className="auth-field">
            <span>{t("auth.usernameLabel")}</span>
            <input
              autoComplete="username"
              name="username"
              onChange={(event) => setUsername(event.target.value)}
              placeholder={t("auth.usernamePlaceholder")}
              required
              type="text"
              value={username}
            />
          </label>
          <label className="auth-field">
            <span>{t("auth.passwordLabel")}</span>
            <input
              autoComplete="current-password"
              name="password"
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t("auth.passwordPlaceholder")}
              required
              type="password"
              value={password}
            />
          </label>
          {submitError ? <EmptyState title={t("auth.loginFailedTitle")} body={submitError} /> : null}
          <div className="panel-footer">
            <button className="button-secondary" disabled={isSubmitting} type="submit">
              {isSubmitting ? t("auth.signingIn") : t("auth.signIn")}
            </button>
          </div>
        </form>
      </Panel>
    </div>
  );
}
