package com.example.solvencynotifier;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.graphics.Color;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.NotificationManagerCompat;

import java.util.Set;

public class MainActivity extends AppCompatActivity {

    private EditText backendUrlEditText;
    private EditText tokenEditText;
    private TextView statusTextView;

    private SharedPreferences getPrefs() {
        return getSharedPreferences(NetworkClient.PREFS_NAME, MODE_PRIVATE);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        backendUrlEditText = findViewById(R.id.edittext_backend_url);
        tokenEditText = findViewById(R.id.edittext_token);
        statusTextView = findViewById(R.id.textview_status);
        Button openSettingsButton = findViewById(R.id.button_open_settings);
        Button saveButton = findViewById(R.id.button_save);

        SharedPreferences prefs = getPrefs();
        String backendUrl = prefs.getString(NetworkClient.KEY_BACKEND_URL, NetworkClient.DEFAULT_BACKEND_URL);
        String token = prefs.getString(NetworkClient.KEY_AUTH_TOKEN, "");
        backendUrlEditText.setText(backendUrl);
        tokenEditText.setText(token);

        openSettingsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Intent intent = new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS);
                startActivity(intent);
            }
        });

        saveButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String url = backendUrlEditText.getText() != null ? backendUrlEditText.getText().toString().trim() : "";
                String tokenValue = tokenEditText.getText() != null ? tokenEditText.getText().toString().trim() : "";
                SharedPreferences.Editor editor = getPrefs().edit();
                if (TextUtils.isEmpty(url)) {
                    editor.putString(NetworkClient.KEY_BACKEND_URL, NetworkClient.DEFAULT_BACKEND_URL);
                } else {
                    editor.putString(NetworkClient.KEY_BACKEND_URL, url);
                }
                editor.putString(NetworkClient.KEY_AUTH_TOKEN, tokenValue);
                editor.apply();
                updateStatus();
            }
        });

        updateStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStatus();
    }

    private void updateStatus() {
        Set<String> enabledPackages = NotificationManagerCompat.getEnabledListenerPackages(this);
        boolean enabled = enabledPackages != null && enabledPackages.contains(getPackageName());
        if (enabled) {
            statusTextView.setText(R.string.status_listener_active);
            statusTextView.setTextColor(Color.parseColor("#388E3C"));
        } else {
            statusTextView.setText(R.string.status_listener_inactive);
            statusTextView.setTextColor(Color.parseColor("#D32F2F"));
        }
    }
}
