(function () {
  "use strict";
  var params = new URLSearchParams(location.hash.slice(1));
  var token = params.get("t") || "";
  var button = document.getElementById("confirm");
  var result = document.getElementById("result");
  if (!token) {
    result.textContent = "This link is incomplete. Open it again from the message you received. / यह लिंक अधूरा है।";
    return;
  }
  button.disabled = false;
  button.addEventListener("click", function () {
    button.disabled = true;
    fetch("/api/v1/public/leads/withdraw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token })
    }).then(function (res) {
      if (res.ok) {
        history.replaceState(null, "", location.pathname);
        result.textContent = "Done. Your details have been deleted. / हो गया। आपकी जानकारी मिटा दी गई है।";
        return;
      }
      result.textContent = res.status === 429
        ? "Too many tries. Please try again in an hour. / बहुत बार कोशिश हुई, एक घंटे बाद फिर करें।"
        : "This link has expired or is not valid. Contact the business directly. / यह लिंक अब मान्य नहीं है।";
      button.disabled = res.status === 400;
    }).catch(function () {
      result.textContent = "Could not reach the server. Try again. / सर्वर से संपर्क नहीं हो सका।";
      button.disabled = false;
    });
  });
})();
