import { useState } from 'react';
import {
  searchCode,
  getClassHierarchy,
  findDefinition,
  findFunction,
  findClass,
  findVariable,
  findContent,
  findRelatedCode,
  getComplexity,
  getTopComplexFunctions,
  findCallers,
  getChangeImpact,
  analyzeRelationships,
  getLanguageSymbols,
  getLanguageStats,
} from '@/lib/api';

function useAsyncState<T>() {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<T>) => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await fn();
      setData(result);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsLoading(false);
    }
  };

  return { data, isLoading, error, run, setData };
}

export function useCodeSearch() {
  const { data, isLoading, error, run } = useAsyncState<any[]>();

  const search = (query: string) =>
    run(async () => {
      const result = await searchCode(query);
      return result.results ?? result ?? [];
    });

  return { data, isLoading, error, search };
}

export function useClassHierarchy() {
  const { data, isLoading, error, run } = useAsyncState<any>();

  const getHierarchy = (className: string) =>
    run(() => getClassHierarchy(className));

  return { data, isLoading, error, getHierarchy };
}

export function useSymbolDefinition() {
  const { data, isLoading, error, run } = useAsyncState<any[]>();

  const findDefinitionHook = (symbolName: string, repoId: string) =>
    run(async () => {
      const result = await findDefinition(symbolName, repoId);
      return result.results ?? result ?? [];
    });

  return { data, isLoading, error, findDefinition: findDefinitionHook };
}

export function useCodeFinder() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    setIsLoading(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsLoading(false);
    }
  };

  return {
    isLoading,
    error,
    findFunction: (name: string, fuzzy = false) =>
      run(() => findFunction(name + (fuzzy ? '&fuzzy=true' : ''))),
    findClass: (name: string, fuzzy = false) =>
      run(() => findClass(name + (fuzzy ? '&fuzzy=true' : ''))),
    findVariable: (name: string) => run(() => findVariable(name)),
    findByContent: (query: string) => run(() => findContent(query)),
    findRelatedCode: (query: string, fuzzy = false, depth = 2) =>
      run(() => findRelatedCode(query, fuzzy, depth)),
  };
}

export function useComplexityAnalysis() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    setIsLoading(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsLoading(false);
    }
  };

  return {
    isLoading,
    error,
    getComplexity: (functionName: string, path?: string) =>
      run(() => getComplexity(functionName, path)),
    getMostComplex: (limit = 10) =>
      run(() => getTopComplexFunctions()),
  };
}

export function useRelationshipAnalysis() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    setIsLoading(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsLoading(false);
    }
  };

  return {
    isLoading,
    error,
    analyzeRelationships: (queryType: string, target: string, context?: string) =>
      run(() => analyzeRelationships(queryType, target, context)),
    findCallers: (name: string) => run(() => findCallers(name)),
    analyzeChangeImpact: (name: string) => run(() => getChangeImpact(name)),
  };
}

export function useLanguageQuery() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    setIsLoading(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsLoading(false);
    }
  };

  return {
    isLoading,
    error,
    getLanguageSymbols: (language: string, symbolType: string, limit = 50) =>
      run(() => getLanguageSymbols(language, symbolType, limit)),
    getLanguageStats: (language?: string) =>
      run(() => getLanguageStats()),
  };
}

export function useAdvancedSearch() {
  const { data, isLoading, error, run } = useAsyncState<any[]>();

  const search = (query: string) =>
    run(async () => {
      const result = await searchCode(query);
      return result.results ?? result ?? [];
    });

  return { data, isLoading, error, search };
}
