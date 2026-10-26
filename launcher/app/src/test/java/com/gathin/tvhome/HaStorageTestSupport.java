package com.gathin.tvhome;

import android.content.SharedPreferences;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;
import java.security.GeneralSecurityException;

import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;

final class HaStorageTestSupport {
    static final String ORIGIN = "https://ha.example.test";
    static final String TOKEN = "synthetic-test-token";

    static final class Keys implements HaCredentials.Keys {
        SecretKey value;
        boolean failCreate, failDelete;
        int creations;

        @Override public SecretKey key(boolean create) throws GeneralSecurityException, IOException {
            if (value != null) return value;
            if (!create) throw new GeneralSecurityException("Synthetic missing key");
            if (failCreate) throw new IOException("Synthetic storage failure");
            KeyGenerator generator = KeyGenerator.getInstance("AES");
            generator.init(256);
            value = generator.generateKey();
            creations++;
            return value;
        }

        @Override public void delete() throws IOException {
            if (failDelete) throw new IOException("Synthetic deletion failure");
            value = null;
        }
    }

    static SharedPreferences failingCommits(SharedPreferences target) {
        return (SharedPreferences) Proxy.newProxyInstance(SharedPreferences.class.getClassLoader(),
                new Class<?>[]{SharedPreferences.class}, (proxy, method, args) -> {
                    if ("edit".equals(method.getName())) {
                        SharedPreferences.Editor editor = target.edit();
                        return Proxy.newProxyInstance(SharedPreferences.Editor.class.getClassLoader(),
                                new Class<?>[]{SharedPreferences.Editor.class}, (owner, call, values) -> {
                                    if ("commit".equals(call.getName())) return false;
                                    Object result = invoke(editor, call, values);
                                    return result == editor ? owner : result;
                                });
                    }
                    return invoke(target, method, args);
                });
    }

    private static Object invoke(Object target, java.lang.reflect.Method method, Object[] args)
            throws Throwable {
        try {
            return method.invoke(target, args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }
}
