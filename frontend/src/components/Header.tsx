'use client';

import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';
import { UserCircleIcon } from '@heroicons/react/24/outline';

export function Header() {
  const { user, isLoading, logout } = useAuth();

  return (
    <header className="bg-white border-b border-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <Link href="/" className="text-xl font-light tracking-tight text-slate-800">
          Legal Search <span className="font-bold text-blue-600">Buddy</span>
        </Link>

        <div className="flex items-center gap-4">
          {isLoading ? (
            <div className="w-8 h-8 rounded-full bg-slate-100 animate-pulse"></div>
          ) : user ? (
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-slate-600">
                <UserCircleIcon className="w-6 h-6" />
                <span>{user.name || user.email}</span>
              </div>
              <button
                onClick={logout}
                className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors"
              >
                Logout
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              className="px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 transition-colors"
            >
              Sign In
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
