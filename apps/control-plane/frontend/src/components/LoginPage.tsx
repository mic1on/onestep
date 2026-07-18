import { FormEvent, useEffect, useMemo, useState } from 'react';
import { LockKeyhole, ShieldCheck } from 'lucide-react';
import { getApiErrorMessage, getConsoleSession, loginConsole } from '../api';
import { useI18n } from '../i18n';
import BrandLogo from './BrandLogo';

interface LoginPageProps {
  onAuthenticated: (nextPath: string) => void;
}

function sanitizeNextPath(candidate: string | null) {
  if (!candidate || !candidate.startsWith('/') || candidate.startsWith('//')) {
    return '/';
  }
  return candidate;
}

export default function LoginPage({ onAuthenticated }: LoginPageProps) {
  const { t } = useI18n();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isChecking, setIsChecking] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [bootstrapRequired, setBootstrapRequired] = useState(false);

  const nextPath = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return sanitizeNextPath(params.get('next'));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function checkSession() {
      setIsChecking(true);
      setError(null);
      try {
        const session = await getConsoleSession();
        if (cancelled) return;
        setBootstrapRequired(session.bootstrap_required);
        if (!session.auth_configured || session.authenticated) {
          onAuthenticated(nextPath);
        }
      } catch (sessionError) {
        if (!cancelled) {
          setError(getApiErrorMessage(sessionError));
        }
      } finally {
        if (!cancelled) {
          setIsChecking(false);
        }
      }
    }

    void checkSession();
    return () => {
      cancelled = true;
    };
  }, [nextPath]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      await loginConsole(username, password);
      onAuthenticated(nextPath);
    } catch (loginError) {
      setError(getApiErrorMessage(loginError));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl items-center justify-center px-6 py-10">
        <section className="grid w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm md:grid-cols-[0.9fr_1.1fr]">
          <div className="border-b border-slate-200 bg-slate-950 p-8 text-white md:border-b-0 md:border-r">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-600 text-white">
                <BrandLogo className="h-9 w-8" decorative />
              </div>
              <div>
                <h1 className="text-lg font-bold">OneStep</h1>
                <p className="text-xs font-semibold text-slate-400">{t('common.controlPlane')}</p>
              </div>
            </div>

            <div className="mt-16 space-y-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-white/10">
                <ShieldCheck className="h-6 w-6 text-indigo-300" />
              </div>
              <div>
                <h2 className="text-2xl font-extrabold tracking-tight">{t('login.consoleSignIn')}</h2>
                <p className="mt-3 max-w-sm text-sm font-medium leading-6 text-slate-300">
                  {t('login.subtitle')}
                </p>
              </div>
            </div>
          </div>

          <div className="p-8 md:p-10">
            <div className="mb-8">
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                <LockKeyhole className="h-5 w-5" />
              </div>
              <h2 className="text-xl font-bold">{t('login.title')}</h2>
              <p className="mt-2 text-sm font-medium text-slate-500">
                {t('login.useAccount')}
              </p>
            </div>

            {bootstrapRequired ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-900">
                {t('login.bootstrapRequired')}
              </div>
            ) : (
              <form className="space-y-5" onSubmit={(event) => void handleSubmit(event)}>
                <label className="block">
                  <span className="text-xs font-bold uppercase tracking-wide text-slate-500">{t('login.username')}</span>
                  <input
                    autoComplete="username"
                    className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    disabled={isChecking || isSubmitting}
                    onChange={(event) => setUsername(event.target.value)}
                    required
                    type="text"
                    value={username}
                  />
                </label>

                <label className="block">
                  <span className="text-xs font-bold uppercase tracking-wide text-slate-500">{t('login.password')}</span>
                  <input
                    autoComplete="current-password"
                    className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                    disabled={isChecking || isSubmitting}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    type="password"
                    value={password}
                  />
                </label>

                {error ? (
                  <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-900">
                    {error}
                  </div>
                ) : null}

                <button
                  className="flex w-full items-center justify-center rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isChecking || isSubmitting}
                  type="submit"
                >
                  {isChecking ? t('common.checkingSession') : isSubmitting ? t('button.signingIn') : t('button.signIn')}
                </button>
              </form>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
