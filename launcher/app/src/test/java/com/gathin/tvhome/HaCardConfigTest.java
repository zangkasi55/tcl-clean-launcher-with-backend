package com.gathin.tvhome;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

import static org.junit.Assert.*;

public class HaCardConfigTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();

    @Test public void acceptsExactlyVersionOneAndAllThreeCardObjects() throws Exception {
        new HaCardConfig(HaTestData.mapping());
        for (Object version : new Object[] {true, "1", 1.0, 0, 2, JSONObject.NULL}) {
            assertThrows(JSONException.class, () -> new HaCardConfig(
                    HaTestData.mapping().put("schema_version", version)));
        }
        for (String kind : HaCardConfig.KINDS) {
            JSONObject missing = HaTestData.mapping();
            missing.remove(kind);
            assertThrows(JSONException.class, () -> new HaCardConfig(missing));
        }
    }

    @Test public void rejectsSecretsUnknownFieldsAndSimulatedTransports() throws Exception {
        for (String key : new String[] {"token", "access_token", "url", "origin"}) {
            assertThrows(JSONException.class, () -> new HaCardConfig(
                    HaTestData.mapping().put(key, "synthetic")));
            for (String kind : HaCardConfig.KINDS) {
                JSONObject document = HaTestData.mapping();
                document.getJSONObject(kind).put(key, "synthetic");
                assertThrows(JSONException.class, () -> new HaCardConfig(document));
            }
        }
        for (String kind : HaCardConfig.KINDS) {
            for (String mode : new String[] {"simulation", "browser_bridge", ""}) {
                JSONObject document = HaTestData.mapping();
                document.getJSONObject(kind).put("enabled", false).put("transport", mode);
                assertThrows(JSONException.class, () -> new HaCardConfig(document));
            }
        }
    }

    @Test public void requiresRealBooleansAndExactEnabledEntities() throws Exception {
        for (Object value : new Object[] {1, "true", JSONObject.NULL}) {
            JSONObject document = HaTestData.mapping();
            document.getJSONObject("vehicle").put("enabled", value);
            assertThrows(JSONException.class, () -> new HaCardConfig(document));
        }
        for (String entity : new String[] {"sensor.test", "binary_sensor.wrong/path", ""}) {
            JSONObject document = HaTestData.mapping();
            document.getJSONObject("vehicle").put("charging_entity", entity);
            assertThrows(JSONException.class, () -> new HaCardConfig(document));
        }
        JSONObject document = HaTestData.mapping();
        document.getJSONObject("weather").remove("entity_id");
        assertThrows(JSONException.class, () -> new HaCardConfig(document));
    }

    @Test public void basicEnergyRequiresCompleteMappingsAndDashboardDoesNotAllowMixedSigns()
            throws Exception {
        JSONObject document = HaTestData.mapping().put("energy", HaTestData.basicEnergy());
        new HaCardConfig(document);
        document.getJSONObject("energy").remove("solar_entity");
        assertThrows(JSONException.class, () -> new HaCardConfig(document));
        document.getJSONObject("energy").put("topology_dashboard", "test-energy")
                .put("topology_view", "Home");
        new HaCardConfig(document);
        document.getJSONObject("energy").put("grid_import_entity", "sensor.import");
        assertThrows(JSONException.class, () -> new HaCardConfig(document));
    }

    @Test public void documentAndAccessorsCannotBeMutatedOutsideTheirOwner() throws Exception {
        JSONObject document = HaTestData.mapping();
        HaCardConfig config = new HaCardConfig(document);
        document.getJSONObject("vehicle").put("enabled", false);
        assertTrue(config.card("vehicle").getBoolean("enabled"));
        config.card("vehicle").put("enabled", false);
        assertTrue(config.card("vehicle").getBoolean("enabled"));
        assertThrows(JSONException.class, () -> config.card("unknown"));
    }

    @Test public void readsOnlyBoundedNonemptyStrictUtf8() throws Exception {
        File file = temporary.newFile("ha-cards.json");
        assertThrows(IOException.class, () -> HaCardConfig.read(file));
        Files.write(file.toPath(), new byte[] {(byte) 0xc3, (byte) 0x28});
        assertThrows(IOException.class, () -> HaCardConfig.read(file));
        Files.write(file.toPath(), new byte[16385]);
        assertThrows(IOException.class, () -> HaCardConfig.read(file));
        String text = HaTestData.mapping().toString();
        Files.write(file.toPath(), text.getBytes(StandardCharsets.UTF_8));
        assertEquals(text, HaCardConfig.read(file));
        Files.write(file.toPath(), new byte[16384]);
        assertEquals(16384, HaCardConfig.read(file).length());
    }
}
