import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div>
      <header
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 24,
          padding: '12px 0',
          borderBottom: '1px solid #8883',
        }}
      >
        <strong style={{ fontSize: 18 }}>Gas Balance</strong>
        <nav style={{ display: 'flex', gap: 16 }}>
          <Link to="/">Series</Link>
          <Link to="/model">Model</Link>
        </nav>
      </header>
      <main style={{ padding: '16px 0' }}>{children}</main>
    </div>
  )
}
