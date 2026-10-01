import { NavLink, Route, Routes } from 'react-router-dom'
import { Queue } from './pages/Queue'
import { Issue } from './pages/Issue'
import { Agents } from './pages/Agents'
import { Board } from './pages/Board'
import { StatusStrip } from './components/StatusStrip'

export default function App() {
  return (
    <div className="app">
      <header className="top">
        <span className="brand">OSS Scout</span>
        <nav>
          <NavLink to="/" end>Queue</NavLink>
          <NavLink to="/board">Board</NavLink>
          <NavLink to="/agents">Agents</NavLink>
        </nav>
      </header>
      <StatusStrip />
      <main>
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/board" element={<Board />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/issues/:id" element={<Issue />} />
        </Routes>
      </main>
    </div>
  )
}
