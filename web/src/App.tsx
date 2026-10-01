import { NavLink, Route, Routes } from 'react-router-dom'
import { Queue } from './pages/Queue'
import { Issue } from './pages/Issue'
import { Agents } from './pages/Agents'

export default function App() {
  return (
    <div className="app">
      <header className="top">
        <strong>OSS Scout</strong>
        <nav>
          <NavLink to="/" end>Queue</NavLink>
          <NavLink to="/agents">Agents</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/issues/:id" element={<Issue />} />
        </Routes>
      </main>
    </div>
  )
}
