package com.coachai.android.ui.auth

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ArrowForward
import androidx.compose.material.icons.rounded.Email
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.Person
import androidx.compose.material.icons.rounded.Visibility
import androidx.compose.material.icons.rounded.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import com.coachai.android.ui.theme.Accent
import com.coachai.android.ui.theme.Background
import com.coachai.android.ui.theme.Border
import com.coachai.android.ui.theme.Card
import com.coachai.android.ui.theme.Danger
import com.coachai.android.ui.theme.Success
import com.coachai.android.ui.theme.TextMuted
import com.coachai.android.ui.theme.TextPrimary
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential.Companion.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
import kotlinx.coroutines.launch

private enum class AuthMode { Login, Register, Reset }

@Composable
fun AuthScreen(
    state: AuthUiState,
    onSignIn: (String, String) -> Unit,
    onRegister: (String, String, String) -> Unit,
    onReset: (String) -> Unit,
    onGoogleToken: (String) -> Unit,
    onGoogleError: (String) -> Unit,
    onConfirmPasswordReset: (String, String) -> Unit,
    onClosePasswordReset: () -> Unit,
    onClearFeedback: () -> Unit,
) {
    var mode by rememberSaveable { mutableStateOf(AuthMode.Login) }
    var name by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var passwordVisible by rememberSaveable { mutableStateOf(false) }

    if (state.passwordResetLinkState != PasswordResetLinkState.None) {
        PasswordResetLinkScreen(
            state = state,
            onConfirm = onConfirmPasswordReset,
            onClose = onClosePasswordReset,
            onClearFeedback = onClearFeedback,
        )
        return
    }

    fun show(next: AuthMode) {
        mode = next
        password = ""
        passwordVisible = false
        onClearFeedback()
    }

    BackHandler(enabled = mode != AuthMode.Login) { show(AuthMode.Login) }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
            .statusBarsPadding()
            .imePadding(),
    ) {
        if (state.loading) {
            CircularProgressIndicator(color = Accent, modifier = Modifier.align(Alignment.Center))
            return@Box
        }

        Column(
            modifier = Modifier
                .align(Alignment.Center)
                .fillMaxWidth()
                .widthIn(max = 480.dp)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 28.dp),
        ) {
            Brand()
            Spacer(Modifier.height(if (mode == AuthMode.Login) 44.dp else 26.dp))

            if (mode != AuthMode.Login) {
                BackButton { show(AuthMode.Login) }
                Spacer(Modifier.height(24.dp))
            }

            Text(
                text = when (mode) {
                    AuthMode.Login -> "Welcome back"
                    AuthMode.Register -> "Create account"
                    AuthMode.Reset -> "Reset password"
                },
                color = TextPrimary,
                fontSize = 42.sp,
                lineHeight = 45.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-1.5).sp,
            )
            Text(
                text = when (mode) {
                    AuthMode.Login -> "Pick up where you left off and keep moving forward."
                    AuthMode.Register -> "Start building better days with Zen Maestro."
                    AuthMode.Reset -> "Enter your email and we’ll send you a password reset link."
                },
                color = TextMuted,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(top = 12.dp, bottom = 30.dp),
            )

            if (mode == AuthMode.Register) {
                AuthField("Name", name, { name = it; onClearFeedback() }, "Your name", Icons.Rounded.Person, KeyboardType.Text, ImeAction.Next)
                Spacer(Modifier.height(18.dp))
            }

            AuthField(
                "Email address",
                email,
                { email = it; onClearFeedback() },
                "you@example.com",
                Icons.Rounded.Email,
                KeyboardType.Email,
                if (mode == AuthMode.Reset) ImeAction.Done else ImeAction.Next,
            )

            if (mode != AuthMode.Reset) {
                Spacer(Modifier.height(18.dp))
                AuthField(
                    label = "Password",
                    value = password,
                    onValueChange = { password = it; onClearFeedback() },
                    placeholder = if (mode == AuthMode.Register) "At least 8 characters" else "Enter your password",
                    icon = Icons.Rounded.Lock,
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done,
                    passwordVisible = passwordVisible,
                    onPasswordVisibility = { passwordVisible = !passwordVisible },
                )
            }

            if (mode == AuthMode.Login) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                    horizontalArrangement = Arrangement.End,
                ) {
                    TextButton(onClick = { show(AuthMode.Reset) }) {
                        Text("Forgot password?", color = Accent, fontWeight = FontWeight.Bold)
                    }
                }
            } else {
                Spacer(Modifier.height(20.dp))
            }

            Feedback(state)
            PrimaryAction(
                label = when (mode) {
                    AuthMode.Login -> "Log in"
                    AuthMode.Register -> "Create account"
                    AuthMode.Reset -> "Send reset link"
                },
                loading = state.submitting,
                onClick = {
                    when (mode) {
                        AuthMode.Login -> onSignIn(email, password)
                        AuthMode.Register -> onRegister(name, email, password)
                        AuthMode.Reset -> onReset(email)
                    }
                },
            )

            if (mode != AuthMode.Reset) {
                OrDivider()
                GoogleButton(!state.submitting, onGoogleToken, onGoogleError)
            }
            AuthFooter(mode, { show(AuthMode.Login) }, { show(AuthMode.Register) })
        }
    }
}

@Composable
private fun PasswordResetLinkScreen(
    state: AuthUiState,
    onConfirm: (String, String) -> Unit,
    onClose: () -> Unit,
    onClearFeedback: () -> Unit,
) {
    var password by rememberSaveable { mutableStateOf("") }
    var confirmation by rememberSaveable { mutableStateOf("") }
    var passwordVisible by rememberSaveable { mutableStateOf(false) }

    BackHandler(onBack = onClose)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
            .statusBarsPadding()
            .imePadding(),
    ) {
        Column(
            modifier = Modifier
                .align(Alignment.Center)
                .fillMaxWidth()
                .widthIn(max = 480.dp)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 28.dp),
        ) {
            Brand()
            Spacer(Modifier.height(26.dp))
            BackButton(onClose)
            Spacer(Modifier.height(24.dp))

            val title = when (state.passwordResetLinkState) {
                PasswordResetLinkState.Verifying -> "Checking link"
                PasswordResetLinkState.Ready -> "Create new password"
                PasswordResetLinkState.Invalid -> "Link expired"
                PasswordResetLinkState.Complete -> "Password changed"
                PasswordResetLinkState.None -> "Reset password"
            }
            val subtitle = when (state.passwordResetLinkState) {
                PasswordResetLinkState.Verifying -> "Making sure this reset link is valid and secure."
                PasswordResetLinkState.Ready -> state.passwordResetEmail
                    ?.let { "Choose a new password for $it." }
                    ?: "Choose a strong password you haven’t used before."
                PasswordResetLinkState.Invalid -> "This link can no longer be used. Request a fresh one to protect your account."
                PasswordResetLinkState.Complete -> "Your account is secure with the new password."
                PasswordResetLinkState.None -> ""
            }
            Text(
                title,
                color = TextPrimary,
                fontSize = 42.sp,
                lineHeight = 45.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-1.5).sp,
            )
            Text(
                subtitle,
                color = TextMuted,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(top = 12.dp, bottom = 30.dp),
            )

            when (state.passwordResetLinkState) {
                PasswordResetLinkState.Verifying -> {
                    CircularProgressIndicator(
                        color = Accent,
                        modifier = Modifier.align(Alignment.CenterHorizontally),
                    )
                    Spacer(Modifier.height(38.dp))
                }
                PasswordResetLinkState.Ready -> {
                    AuthField(
                        label = "New password",
                        value = password,
                        onValueChange = { password = it; onClearFeedback() },
                        placeholder = "At least 8 characters",
                        icon = Icons.Rounded.Lock,
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Next,
                        passwordVisible = passwordVisible,
                        onPasswordVisibility = { passwordVisible = !passwordVisible },
                    )
                    Spacer(Modifier.height(18.dp))
                    AuthField(
                        label = "Confirm password",
                        value = confirmation,
                        onValueChange = { confirmation = it; onClearFeedback() },
                        placeholder = "Enter it again",
                        icon = Icons.Rounded.Lock,
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Done,
                        passwordVisible = passwordVisible,
                        onPasswordVisibility = { passwordVisible = !passwordVisible },
                    )
                    Spacer(Modifier.height(20.dp))
                    Feedback(state)
                    PrimaryAction(
                        label = "Change password",
                        loading = state.submitting,
                        onClick = { onConfirm(password, confirmation) },
                    )
                }
                PasswordResetLinkState.Invalid -> {
                    Feedback(state)
                    PrimaryAction("Request a new link", false, onClose)
                }
                PasswordResetLinkState.Complete -> {
                    Feedback(state)
                    PrimaryAction("Back to log in", false, onClose)
                }
                PasswordResetLinkState.None -> Unit
            }
        }
    }
}

@Composable
private fun Brand() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier.size(38.dp).background(Accent, RoundedCornerShape(13.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Text("Z", color = Color.White, fontWeight = FontWeight.ExtraBold, fontSize = 19.sp)
        }
        Text(
            "Zen Maestro",
            color = TextPrimary,
            fontWeight = FontWeight.ExtraBold,
            fontSize = 18.sp,
            modifier = Modifier.padding(start = 10.dp),
        )
    }
}

@Composable
private fun BackButton(onClick: () -> Unit) {
    IconButton(
        onClick = onClick,
        modifier = Modifier.size(46.dp).background(Card, RoundedCornerShape(14.dp)),
    ) {
        Icon(Icons.Rounded.ArrowBack, contentDescription = "Back to log in", tint = TextPrimary)
    }
}

@Composable
private fun AuthField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    icon: ImageVector,
    keyboardType: KeyboardType,
    imeAction: ImeAction,
    passwordVisible: Boolean? = null,
    onPasswordVisibility: (() -> Unit)? = null,
) {
    Column {
        Text(
            label,
            color = TextPrimary,
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(start = 2.dp, bottom = 9.dp),
        )
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            placeholder = { Text(placeholder, color = TextMuted) },
            leadingIcon = { Icon(icon, contentDescription = null, tint = TextMuted) },
            trailingIcon = if (passwordVisible != null && onPasswordVisibility != null) {
                {
                    IconButton(onClick = onPasswordVisibility) {
                        Icon(
                            if (passwordVisible) Icons.Rounded.VisibilityOff else Icons.Rounded.Visibility,
                            contentDescription = if (passwordVisible) "Hide password" else "Show password",
                            tint = TextMuted,
                        )
                    }
                }
            } else null,
            visualTransformation = if (passwordVisible == false) PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = KeyboardOptions(keyboardType = keyboardType, imeAction = imeAction),
            singleLine = true,
            shape = RoundedCornerShape(20.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Accent,
                unfocusedBorderColor = Border,
                focusedContainerColor = Card,
                unfocusedContainerColor = Card,
                cursorColor = Accent,
                focusedTextColor = TextPrimary,
                unfocusedTextColor = TextPrimary,
            ),
            modifier = Modifier.fillMaxWidth().height(68.dp),
        )
    }
}

@Composable
private fun Feedback(state: AuthUiState) {
    val message = state.error ?: state.message ?: return
    Text(
        text = message,
        color = if (state.error != null) Danger else Success,
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(start = 3.dp, bottom = 14.dp),
    )
}

@Composable
private fun PrimaryAction(label: String, loading: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        enabled = !loading,
        shape = RoundedCornerShape(20.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = Accent,
            contentColor = Color.White,
            disabledContainerColor = Accent.copy(alpha = 0.55f),
            disabledContentColor = Color.White,
        ),
        modifier = Modifier.fillMaxWidth().height(66.dp),
    ) {
        if (loading) {
            CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.size(24.dp))
        } else {
            Text(label, fontWeight = FontWeight.ExtraBold, fontSize = 17.sp)
            Icon(Icons.Rounded.ArrowForward, null, modifier = Modifier.padding(start = 12.dp).size(21.dp))
        }
    }
}

@Composable
private fun OrDivider() {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 26.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        HorizontalDivider(modifier = Modifier.weight(1f), color = Border)
        Text("or", color = TextMuted, modifier = Modifier.padding(horizontal = 16.dp))
        HorizontalDivider(modifier = Modifier.weight(1f), color = Border)
    }
}

@Composable
private fun GoogleButton(enabled: Boolean, onToken: (String) -> Unit, onError: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var requesting by remember { mutableStateOf(false) }

    Button(
        onClick = {
            scope.launch {
                requesting = true
                try {
                    val clientIdResource = context.resources.getIdentifier("default_web_client_id", "string", context.packageName)
                    if (clientIdResource == 0) {
                        onError("Google sign-in isn't ready yet. Try email and password for now.")
                        return@launch
                    }
                    val option = GetSignInWithGoogleOption.Builder(context.getString(clientIdResource)).build()
                    val request = GetCredentialRequest.Builder().addCredentialOption(option).build()
                    val result = CredentialManager.create(context).getCredential(
                        context = context.findActivity() ?: context,
                        request = request,
                    )
                    val credential = result.credential
                    if (credential is CustomCredential && credential.type == TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                        onToken(GoogleIdTokenCredential.createFrom(credential.data).idToken)
                    } else {
                        onError("Google sign-in couldn't verify this account. Try again.")
                    }
                } catch (_: GetCredentialCancellationException) {
                    // Closing the Google account picker is not an authentication failure.
                } catch (_: GetCredentialException) {
                    onError("Google sign-in couldn't be completed. Try again in a moment.")
                } catch (_: Exception) {
                    onError("Google sign-in couldn't be completed. Try again.")
                } finally {
                    requesting = false
                }
            }
        },
        enabled = enabled && !requesting,
        shape = RoundedCornerShape(20.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = Card,
            contentColor = TextPrimary,
            disabledContainerColor = Card,
            disabledContentColor = TextMuted,
        ),
        border = ButtonDefaults.outlinedButtonBorder(enabled = enabled && !requesting),
        modifier = Modifier.fillMaxWidth().height(64.dp),
    ) {
        if (requesting) {
            CircularProgressIndicator(color = Accent, strokeWidth = 2.dp, modifier = Modifier.size(23.dp))
        } else {
            Text("G", color = Color(0xFF4285F4), fontSize = 21.sp, fontWeight = FontWeight.ExtraBold)
            Text("Continue with Google", modifier = Modifier.padding(start = 13.dp), fontWeight = FontWeight.Bold, fontSize = 16.sp)
        }
    }
}

@Composable
private fun AuthFooter(mode: AuthMode, onLogin: () -> Unit, onRegister: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = 25.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        when (mode) {
            AuthMode.Login -> {
                Text("New here?", color = TextMuted)
                TextButton(onClick = onRegister) { Text("Create account", color = Accent, fontWeight = FontWeight.Bold) }
            }
            AuthMode.Register -> {
                Text("Already a member?", color = TextMuted)
                TextButton(onClick = onLogin) { Text("Log in", color = Accent, fontWeight = FontWeight.Bold) }
            }
            AuthMode.Reset -> {
                Text("Remembered it?", color = TextMuted)
                TextButton(onClick = onLogin) { Text("Back to log in", color = Accent, fontWeight = FontWeight.Bold) }
            }
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}
