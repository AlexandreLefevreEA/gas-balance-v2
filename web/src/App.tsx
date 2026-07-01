import { Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ModelExplorer } from './pages/ModelExplorer'
import { SeriesExplorer } from './pages/SeriesExplorer'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<SeriesExplorer />} />
        <Route path="/model" element={<ModelExplorer />} />
      </Routes>
    </Layout>
  )
}
