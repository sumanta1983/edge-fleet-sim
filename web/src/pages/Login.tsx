import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, tokenStore, unwrap } from "../api/client";
import { ErrorBox } from "../ui";

const DEMO_USERS = ["operator@tenant-a.example", "viewer@tenant-a.example", "operator@tenant-b.example"];

export default function Login() {
  const [email, setEmail] = useState(DEMO_USERS[0]!);
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const login = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/auth/login", {
          // OAuth2 password flow: a form post, not JSON.
          body: { username: email, password, scope: "" },
          bodySerializer: (body) => new URLSearchParams(body as Record<string, string>),
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
        }),
      ),
    onSuccess: (token) => {
      tokenStore.set(token.access_token);
      queryClient.clear();
      navigate("/");
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    login.mutate();
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>edgefleet-sim</h1>
        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} list="demo-users" autoComplete="username" />
          <datalist id="demo-users">
            {DEMO_USERS.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        <ErrorBox error={login.error} />
        <button type="submit" disabled={login.isPending}>{login.isPending ? "Signing in…" : "Sign in"}</button>
        <p className="hint">Local demo: every seeded user's password is <code>dev-password</code>.</p>
      </form>
    </div>
  );
}
