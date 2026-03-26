package com.example.solvencynotifier;

import android.content.Context;
import android.content.SharedPreferences;
import android.text.TextUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public class NetworkClient {

    public static final String PREFS_NAME = "solvency_prefs";
    public static final String KEY_BACKEND_URL = "backend_url";
    public static final String KEY_AUTH_TOKEN = "auth_token";
    public static final String KEY_LAST_NOTIFICATION_KEY = "last_notification_key";
    public static final String DEFAULT_BACKEND_URL = "http://192.168.1.5:5000/api/receive_sms";

    private static final MediaType JSON_MEDIA_TYPE = MediaType.get("application/json; charset=utf-8");

    private static NetworkClient instance;

    private final OkHttpClient client;
    private final Context appContext;

    private NetworkClient(Context context) {
        this.appContext = context.getApplicationContext();
        this.client = new OkHttpClient.Builder().build();
    }

    public static synchronized NetworkClient getInstance(Context context) {
        if (instance == null) {
            instance = new NetworkClient(context);
        }
        return instance;
    }

    public void sendNotificationMessage(String message) {
        if (TextUtils.isEmpty(message)) {
            return;
        }
        SharedPreferences prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String backendUrl = prefs.getString(KEY_BACKEND_URL, DEFAULT_BACKEND_URL);
        String token = prefs.getString(KEY_AUTH_TOKEN, "");

        if (TextUtils.isEmpty(backendUrl)) {
            return;
        }

        JSONObject json = new JSONObject();
        try {
            json.put("message", message);
        } catch (JSONException e) {
            return;
        }

        RequestBody body = RequestBody.create(json.toString(), JSON_MEDIA_TYPE);
        Request.Builder requestBuilder = new Request.Builder()
                .url(backendUrl)
                .post(body);

        if (!TextUtils.isEmpty(token)) {
            requestBuilder.addHeader("Authorization", "Bearer " + token);
        }

        Request request = requestBuilder.build();
        Call call = client.newCall(request);
        enqueueWithRetry(call, false);
    }

    private void enqueueWithRetry(final Call call, final boolean hasRetried) {
        call.enqueue(new Callback() {
            @Override
            public void onFailure(Call call, IOException e) {
                if (!hasRetried) {
                    Call newCall = call.clone();
                    enqueueWithRetry(newCall, true);
                }
            }

            @Override
            public void onResponse(Call call, Response response) {
                response.close();
            }
        });
    }
}
