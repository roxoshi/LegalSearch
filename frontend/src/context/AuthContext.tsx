'use client';

<<<<<<< HEAD
import { createContext, useContext, useState, useEffect, ReactNode, useCallback } from 'react';
import { getApiUrl } from '@/lib/api';
=======
import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
>>>>>>> f784922c5b9f718b8c700cd74f35f0b3c9c52898

interface User {
  id: number;
  email: string;
  name: string | null;
  oauth_provider: string | null;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name?: string) => Promise<void>;
  loginWithGoogle: (googleAccessToken: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

<<<<<<< HEAD
  const fetchUser = useCallback(async (authToken: string) => {
    try {
      const apiUrl = getApiUrl();
=======
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  // Load token from localStorage on mount
  useEffect(() => {
    const storedToken = localStorage.getItem('auth_token');
    if (storedToken) {
      setToken(storedToken);
      fetchUser(storedToken);
    } else {
      setIsLoading(false);
    }
  }, []);

  const fetchUser = async (authToken: string) => {
    try {
>>>>>>> f784922c5b9f718b8c700cd74f35f0b3c9c52898
      const res = await fetch(`${apiUrl}/auth/me`, {
        headers: { Authorization: `Bearer ${authToken}` }
      });
      if (res.ok) {
        const userData = await res.json();
        setUser(userData);
      } else {
        // Token invalid, clear it
        localStorage.removeItem('auth_token');
        setToken(null);
      }
    } catch (err) {
      console.error('Failed to fetch user', err);
    } finally {
      setIsLoading(false);
    }
<<<<<<< HEAD
  }, []);

  // Load token from localStorage on mount
  useEffect(() => {
    const storedToken = localStorage.getItem('auth_token');
    if (storedToken) {
      setToken(storedToken);
      fetchUser(storedToken);
    } else {
      setIsLoading(false);
    }
  }, [fetchUser]);

  const login = async (email: string, password: string) => {
    const apiUrl = getApiUrl();
=======
  };

  const login = async (email: string, password: string) => {
>>>>>>> f784922c5b9f718b8c700cd74f35f0b3c9c52898
    const formData = new URLSearchParams();
    formData.append('username', email);
    formData.append('password', password);

    const res = await fetch(`${apiUrl}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Login failed');
    }

    const data = await res.json();
    localStorage.setItem('auth_token', data.access_token);
    setToken(data.access_token);
    await fetchUser(data.access_token);
  };

  const signup = async (email: string, password: string, name?: string) => {
<<<<<<< HEAD
    const apiUrl = getApiUrl();
=======
>>>>>>> f784922c5b9f718b8c700cd74f35f0b3c9c52898
    const res = await fetch(`${apiUrl}/auth/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, name })
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Signup failed');
    }

    // Auto-login after signup
    await login(email, password);
  };

  const loginWithGoogle = async (googleAccessToken: string) => {
<<<<<<< HEAD
    const apiUrl = getApiUrl();
=======
>>>>>>> f784922c5b9f718b8c700cd74f35f0b3c9c52898
    const res = await fetch(`${apiUrl}/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: googleAccessToken })
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Google login failed');
    }

    const data = await res.json();
    localStorage.setItem('auth_token', data.access_token);
    setToken(data.access_token);
    await fetchUser(data.access_token);
  };

  const logout = () => {
    localStorage.removeItem('auth_token');
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, token, isLoading, login, signup, loginWithGoogle, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
