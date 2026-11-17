/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Navy + professional blue. Keep the token names: every component uses them.
        sidebar: "#0B1F3A",
        navy: { DEFAULT: "#0B1F3A", soft: "#13294B" },
        primary: { DEFAULT: "#1D4ED8", hover: "#1E40AF", soft: "#EFF6FF" },
        page: "#F7F9FC",
        ink: "#0F172A",
        muted: "#5B6B82",
        success: { DEFAULT: "#16A34A", text: "#166534", soft: "#F0FDF4" },
        warning: { DEFAULT: "#D97706", text: "#92400E", soft: "#FFFBEB" },
        danger: { DEFAULT: "#DC2626", text: "#991B1B", soft: "#FEF2F2" },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,0.04), 0 2px 8px rgba(15,23,42,0.05)",
      },
    },
  },
  plugins: [],
};
