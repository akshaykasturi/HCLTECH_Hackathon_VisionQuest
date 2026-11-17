import { Link } from "react-router-dom";
import { Clock } from "lucide-react";
import { APP_NAME } from "../lib/nav";

/** Employee home. The complaint form and tracking are the next pages to build (see docs/PLAN.md). */
export function HomePage() {
  return (
    <section className="bg-gradient-to-b from-primary-soft/60 to-white">
      <div className="mx-auto max-w-3xl px-4 py-20 text-center md:px-6 md:py-28">
        <p className="text-sm font-semibold uppercase tracking-wide text-primary">{APP_NAME} IT Support</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-ink md:text-5xl">How can we help you today?</h1>
        <p className="mx-auto mt-4 max-w-xl text-base text-muted md:text-lg">
          Report an IT problem and our support team will help you get back to work.
        </p>
        <div className="mx-auto mt-8 flex max-w-md items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 text-left shadow-card">
          <Clock className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
          <p className="text-sm text-slate-700">
            Online complaint submission is coming soon. IT staff can already{" "}
            <Link to="/login" className="font-medium text-primary underline-offset-2 hover:underline">sign in</Link>{" "}
            to analyse tickets.
          </p>
        </div>
      </div>
    </section>
  );
}
