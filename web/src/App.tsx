import { Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { SeriesExplorer } from './pages/SeriesExplorer'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<SeriesExplorer />} />
      </Routes>
    </Layout>
  )
}
