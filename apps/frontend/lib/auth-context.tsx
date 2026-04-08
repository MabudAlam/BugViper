"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import {
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
  GithubAuthProvider,
  type Auth,
  type User,
} from "firebase/auth";
import { getAuth, getGithubProvider } from "@/lib/firebase";
import { loginUser, ensureUser } from "@/lib/api";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  githubToken: string | null;
  signInWithGitHub: () => Promise<void>;
  signOut: () => Promise<void>;
  getIdToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const GH_TOKEN_KEY = "bugviper_gh_token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [githubToken, setGithubToken] = useState<string | null>(null);
  const authRef = useRef<Auth | null>(null);

  function getFirebaseAuth() {
    if (!authRef.current) {
      authRef.current = getAuth();
    }
    return authRef.current;
  }

  const getIdToken = useCallback(async () => {
    const auth = getFirebaseAuth();
    if (!auth.currentUser) return null;
    return auth.currentUser.getIdToken();
  }, []);

  useEffect(() => {
    const auth = getFirebaseAuth();

    // Restore GitHub token from sessionStorage
    const stored = sessionStorage.getItem(GH_TOKEN_KEY);
    if (stored) setGithubToken(stored);

    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      setUser(firebaseUser);
      setLoading(false);

      if (firebaseUser) {
        try {
          await ensureUser();
        } catch {
          // Non-critical
        }
      } else {
        // Signed out — clear stored token
        sessionStorage.removeItem(GH_TOKEN_KEY);
        setGithubToken(null);
      }
    });

    return unsubscribe;
  }, [getIdToken]);

  const signInWithGitHub = useCallback(async () => {
    const auth = getFirebaseAuth();
    const provider = getGithubProvider();
    const result = await signInWithPopup(auth, provider);
    const credential = GithubAuthProvider.credentialFromResult(result);
    const ghToken = credential?.accessToken;

    if (ghToken) {
      setGithubToken(ghToken);
      sessionStorage.setItem(GH_TOKEN_KEY, ghToken);
      await loginUser({ github_access_token: ghToken });
    }
  }, []);

  const signOut = useCallback(async () => {
    const auth = getFirebaseAuth();
    sessionStorage.removeItem(GH_TOKEN_KEY);
    setGithubToken(null);
    await firebaseSignOut(auth);
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, loading, githubToken, signInWithGitHub, signOut, getIdToken }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
