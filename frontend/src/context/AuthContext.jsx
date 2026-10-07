import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchMe, login as apiLogin, logout as apiLogout } from "../api/auth.js";
import { setUnauthorizedHandler } from "../api/client.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState({ status: "loading", user: null });

  useEffect(() => {
    let alive = true;
    setUnauthorizedHandler(() => {
      queryClient.clear();
      setState({ status: "anonymous", user: null });
    });
    fetchMe()
      .then((user) => alive && setState({ status: "authenticated", user }))
      .catch(() => alive && setState({ status: "anonymous", user: null }));
    return () => {
      alive = false;
      setUnauthorizedHandler(null);
    };
  }, [queryClient]);

  const login = useCallback(
    async (username, password) => {
      const user = await apiLogin(username, password);
      queryClient.clear(); // never show the previous person's cached data
      setState({ status: "authenticated", user });
      return user;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      queryClient.clear();
      setState({ status: "anonymous", user: null });
    }
  }, [queryClient]);

  const value = useMemo(() => ({ ...state, login, logout }), [state, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
