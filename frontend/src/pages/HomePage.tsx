import { Link } from "react-router-dom";
import { Clock, LogIn } from "lucide-react";
import { APP_NAME } from "../lib/nav";

/** Employee home. The complaint form and tracking are designed (docs/ARCHITECTURE.md) but not built, so this page
 *  says so honestly and points IT staff to the analyst sign-in. */
export function HomePage() {
  return (
    <section className="bg-gradient-to-b from-primary-soft/60 to-white">
      <div className="mx-auto max-w-3xl px-4 py-20 text-center md:px-6 md:py-28">
        <p className="text-sm font-semibold uppercase tracking-wide text-primary">{APP_NAME} IT Support</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-ink md:text-5xl">How can we help you today?</h1>
        <p className="mx-auto mt-4 max-w-xl text-base text-muted md:text-lg">
          Report an IT problem and our support team will help you get back to work.
        </p>
        <div className="mt-8 flex flex-col items-center gap-3">
          <Link to="/login"
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-3 text-base font-semibold text-white shadow-card hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
            <LogIn className="h-5 w-5" aria-hidden />
            IT staff: sign in to analyse tickets
          </Link>
          <p className="text-sm text-muted">Analysts and leads use the ticket assistant after signing in.</p>
        </div>
        <div className="mx-auto mt-10 flex max-w-md items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 text-left shadow-card">
          <Clock className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
          <p className="text-sm text-slate-700">
            Online complaint submission for employees is coming soon: it is designed but not built yet.
          </p>
        </div>
      </div>
    </section>
  );
}
