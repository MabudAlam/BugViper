import { initializeApp, getApps, getApp, type FirebaseApp } from "firebase/app";
import { getAuth, GithubAuthProvider, type Auth } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyCD964AoX_VoFcfWUeyRfkhgFJwPp2kCB4",
  authDomain: "mealai-f58b5.firebaseapp.com",
  projectId: "mealai-f58b5",
  storageBucket: "mealai-f58b5.firebasestorage.app",
  messagingSenderId: "760266971868",
  appId: "1:760266971868:web:05e8fea3e7ced1baed7219",
};

function getFirebaseApp(): FirebaseApp {
  return getApps().length ? getApp() : initializeApp(firebaseConfig);
}

function getFirebaseAuth(): Auth {
  return getAuth(getFirebaseApp());
}

function getGithubProvider(): GithubAuthProvider {
  const provider = new GithubAuthProvider();
  provider.addScope("read:user");
  provider.addScope("repo");
  return provider;
}

export { getFirebaseAuth as getAuth, getGithubProvider };
