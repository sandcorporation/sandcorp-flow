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
        // HMI 전용 토큰(index.css 머리말 참고)
        surface: 'hsl(var(--surface))',
        sunken: 'hsl(var(--sunken))',
        raised: 'hsl(var(--raised))',
        'line-soft': 'hsl(var(--line-soft))',
        grid: 'hsl(var(--grid))',
        ink: 'hsl(var(--ink))',
        run: 'hsl(var(--run))',
        ok: 'hsl(var(--ok))',
        alarm: 'hsl(var(--alarm))',
        hazard: 'hsl(var(--hazard))',
        head: { DEFAULT: 'hsl(var(--head))', foreground: 'hsl(var(--head-ink))' },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'var(--radius)',
        sm: 'var(--radius)',
      },
      boxShadow: { sm: 'none', DEFAULT: 'none', md: 'none', lg: 'none' },
      fontFamily: {
        sans: 'var(--font-sans)',
        cond: 'var(--font-cond)',
        mono: 'var(--font-mono)',
      },
    },
  },
  plugins: [],
}
