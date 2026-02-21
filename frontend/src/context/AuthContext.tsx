'use client';

import { createContext, useContext, useState, useEffect, ReactNode, useCallback } from 'react';
import { getApiUrl } from '@/lib/api';

interface User {
  id: string;
  first_name: string;
  last_name: string;
  year_of_birth: number | null;
}

interface ProfileData {
  first_name: string;
  last_name: string;
  year_of_birth?: number | null;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  requestOtp: (identifier: string) => Promise<void>;
  verifyOtp: (identifier: string, otp: string, profile?: ProfileData) => Promise<{ needs_profile: boolean }>;
  loginWithGoogle: (credential: string) => Promise<{ needs_profile: boolean }>;
  updateProfile: (profile: ProfileData) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchUser = useCallback(async () => {
    try {
      const apiUrl = getApiUrl();
      const res = await fetch(`${apiUrl}/auth/me`, {
        credentials: 'include',
      });
      if (res.ok) {
        const userData = await res.json();
        setUser(userData);
      } else {
        setUser(null);
      }
    } catch (err) {
      console.error('Failed to fetch user', err);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  const requestOtp = async (identifier: string) => {
    const apiUrl = getApiUrl();
    const res = await fetch(`${apiUrl}/auth/request-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identifier }),
      credentials: 'include',
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to send OTP');
    }
  };

  const verifyOtp = async (
    identifier: string,
    otp: string,
    profile?: ProfileData
  ): Promise<{ needs_profile: boolean }> => {
    const apiUrl = getApiUrl();
    const res = await fetch(`${apiUrl}/auth/verify-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identifier, otp, ...profile }),
      credentials: 'include',
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'OTP verification failed');
    }

    const data = await res.json();
    if (!data.needs_profile) {
      setUser(data.user);
    }
    return { needs_profile: data.needs_profile };
  };

  const loginWithGoogle = async (credential: string): Promise<{ needs_profile: boolean }> => {
    const apiUrl = getApiUrl();
    const res = await fetch(`${apiUrl}/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential }),
      credentials: 'include',
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Google login failed');
    }

    const data = await res.json();
    if (!data.needs_profile) {
      setUser(data.user);
    }
    return { needs_profile: data.needs_profile };
  };

  const updateProfile = async (profile: ProfileData) => {
    const apiUrl = getApiUrl();
    const res = await fetch(`${apiUrl}/auth/profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(profile),
      credentials: 'include',
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Profile update failed');
    }

    const userData = await res.json();
    setUser(userData);
  };

  const logout = async () => {
    try {
      const apiUrl = getApiUrl();
      await fetch(`${apiUrl}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch (err) {
      console.error('Logout request failed', err);
    }
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, requestOtp, verifyOtp, loginWithGoogle, updateProfile, logout }}>
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
