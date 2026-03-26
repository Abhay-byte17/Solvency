package com.example.solvencynotifier;

import android.app.Notification;
import android.content.Context;
import android.content.SharedPreferences;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.text.TextUtils;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

public class NotificationListener extends NotificationListenerService {

    private static final Set<String> ALLOWED_PACKAGES = new HashSet<>(
            Arrays.asList(
                    "com.google.android.apps.messaging",
                    "com.android.mms",
                    "com.samsung.android.messaging",
                    "com.whatsapp"
            )
    );

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null) {
            return;
        }
        String packageName = sbn.getPackageName();
        if (!ALLOWED_PACKAGES.contains(packageName)) {
            return;
        }
        Notification notification = sbn.getNotification();
        if (notification == null) {
            return;
        }

        CharSequence title = notification.extras.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence text = notification.extras.getCharSequence(Notification.EXTRA_TEXT);
        CharSequence bigText = notification.extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
        CharSequence[] lines = notification.extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES);

        StringBuilder builder = new StringBuilder();

        if (!TextUtils.isEmpty(title)) {
            builder.append(title);
        }

        if (!TextUtils.isEmpty(text)) {
            if (builder.length() > 0) {
                builder.append(" - ");
            }
            builder.append(text);
        }

        if (lines != null && lines.length > 0) {
            StringBuilder linesBuilder = new StringBuilder();
            for (CharSequence line : lines) {
                if (!TextUtils.isEmpty(line)) {
                    if (linesBuilder.length() > 0) {
                        linesBuilder.append(" | ");
                    }
                    linesBuilder.append(line);
                }
            }
            if (linesBuilder.length() > 0) {
                if (builder.length() > 0) {
                    builder.append(" - ");
                }
                builder.append(linesBuilder.toString());
            }
        } else if (!TextUtils.isEmpty(bigText) && (text == null || !bigText.toString().equals(text.toString()))) {
            if (builder.length() > 0) {
                builder.append(" - ");
            }
            builder.append(bigText);
        }

        String message = builder.toString();
        if (TextUtils.isEmpty(message)) {
            return;
        }

        String key = sbn.getKey();
        if (TextUtils.isEmpty(key)) {
            key = buildFallbackKey(sbn);
        }

        if (isDuplicate(this, key)) {
            return;
        }

        saveLastNotificationKey(this, key);
        NetworkClient.getInstance(this).sendNotificationMessage(message);
    }

    private String buildFallbackKey(StatusBarNotification sbn) {
        String tag = sbn.getTag();
        String safeTag = tag == null ? "" : tag;
        return sbn.getPackageName() + ":" + sbn.getId() + ":" + safeTag + ":" + sbn.getPostTime();
    }

    private static boolean isDuplicate(Context context, String key) {
        SharedPreferences prefs = context.getSharedPreferences(NetworkClient.PREFS_NAME, Context.MODE_PRIVATE);
        String lastKey = prefs.getString(NetworkClient.KEY_LAST_NOTIFICATION_KEY, null);
        return key != null && key.equals(lastKey);
    }

    private static void saveLastNotificationKey(Context context, String key) {
        SharedPreferences prefs = context.getSharedPreferences(NetworkClient.PREFS_NAME, Context.MODE_PRIVATE);
        prefs.edit().putString(NetworkClient.KEY_LAST_NOTIFICATION_KEY, key).apply();
    }
}
