/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        secondary: { DEFAULT: 'hsl(var(--secondary))', foreground: 'hsl(var(--secondary-foreground))' },
        destructive: { DEFAULT: 'hsl(var(--destructive))', foreground: 'hsl(var(--destructive-foreground))' },
        muted: { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        accent: { DEFAULT: 'hsl(var(--accent))', foreground: 'hsl(var(--accent-foreground))' },
        card: { DEFAULT: 'hsl(var(--card))', foreground: 'hsl(var(--card-foreground))' },
        // HMI 전용 토큰 — 모두 index.css의 --flow-* 를 가리킨다
        surface: 'hsl(var(--flow-surface))',
        sunken: 'hsl(var(--flow-sunken))',
        raised: 'hsl(var(--flow-raised))',
        'line-soft': 'hsl(var(--flow-line-soft))',
        grid: 'hsl(var(--flow-grid))',
        ink: 'hsl(var(--flow-ink))',
        run: 'hsl(var(--flow-run))',
        ok: 'hsl(var(--flow-ok))',
        alarm: 'hsl(var(--flow-alarm))',
        hazard: 'hsl(var(--flow-hazard))',
        head: { DEFAULT: 'hsl(var(--flow-head))', foreground: 'hsl(var(--flow-head-ink))' },
      },
      borderRadius: {
        lg: 'var(--flow-radius)',
        md: 'var(--flow-radius)',
        sm: 'var(--flow-radius)',
      },
      boxShadow: { sm: 'none', DEFAULT: 'none', md: 'none', lg: 'none' },
      fontFamily: {
        sans: 'var(--flow-font-sans)',
        cond: 'var(--flow-font-cond)',
        mono: 'var(--flow-font-mono)',
      },
    },
  },
  plugins: [],
}
