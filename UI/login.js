"use strict";

const form = document.getElementById("loginForm");
const username = document.getElementById("username");
const password = document.getElementById("password");
const togglePassword = document.getElementById("togglePassword");
const passwordToggleText = document.getElementById("passwordToggleText");
const passwordIcon = document.getElementById("passwordIcon");
const loginError = document.getElementById("loginError");
const loginButton = document.getElementById("loginButton");

togglePassword.addEventListener("click", () => {
  const willShow = password.type === "password";
  password.type = willShow ? "text" : "password";
  togglePassword.setAttribute("aria-pressed", String(willShow));
  togglePassword.setAttribute("aria-label", willShow ? "Ẩn mật khẩu" : "Hiển thị mật khẩu");
  passwordToggleText.textContent = willShow ? "Ẩn" : "Hiện";
  passwordIcon.textContent = willShow ? "⊘" : "◉";
  password.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  if (!username.value.trim() || !password.value) {
    loginError.textContent = "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.";
    return;
  }

  loginButton.disabled = true;
  loginButton.textContent = "Đang đăng nhập...";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "Đăng nhập không thành công.");
    window.location.replace("/dashboard");
  } catch (error) {
    loginError.textContent = error.message;
    password.select();
  } finally {
    loginButton.disabled = false;
    loginButton.textContent = "Đăng nhập";
  }
});
