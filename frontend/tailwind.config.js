/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        sidebar: "#0F172A",
        primary: { DEFAULT: "#4F46E5", hover: "#4338CA", soft: "#EEF2FF" },
        page: "#F8FAFC",
        ink: "#0F172A",
        muted: "#64748B",
        success: { DEFAULT: "#10B981", text: "#047857", soft: "#ECFDF5" },
        warning: { DEFAULT: "#F59E0B", text: "#B45309", soft: "#FFFBEB" },
        danger: { DEFAULT: "#EF4444", text: "#B91C1C", soft: "#FEF2F2" },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: { card: "0 1px 2px rgba(15,23,42,0.04), 0 1px 3px rgba(15,23,42,0.06)" },
    },
  },
  plugins: [],
};
