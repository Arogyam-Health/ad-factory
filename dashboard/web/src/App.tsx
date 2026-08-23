import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "@/components/Shell";
import { StudioPage } from "@/pages/Studio";
import { ConfigPage } from "@/pages/Config";
import { OrganizationsPage } from "@/pages/Organizations";
import { TracesPage } from "@/pages/Traces";
import { ProfilePage } from "@/pages/Profile";
import { AdminPage } from "@/pages/Admin";
import { InvitePage } from "@/pages/Invite";

export function App() {
  return (
    <Routes>
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route
        path="*"
        element={
          <Shell>
            <Routes>
              <Route path="/" element={<StudioPage />} />
              <Route path="/config" element={<ConfigPage />} />
              <Route path="/config.html" element={<Navigate to="/config" replace />} />
              <Route path="/organizations" element={<OrganizationsPage />} />
              <Route path="/organizations.html" element={<Navigate to="/organizations" replace />} />
              <Route path="/traces" element={<TracesPage />} />
              <Route path="/traces.html" element={<Navigate to="/traces" replace />} />
              <Route path="/profile" element={<ProfilePage />} />
              <Route path="/profile.html" element={<Navigate to="/profile" replace />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="/admin.html" element={<Navigate to="/admin" replace />} />
            </Routes>
          </Shell>
        }
      />
    </Routes>
  );
}
