package ai.scionsix.orchestrator;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.TextView;

/** Minimal SCIONSIX AI shell — proves the APK builds and installs. */
public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        TextView tv = new TextView(this);
        tv.setText("SCIONSIX AI");
        tv.setTextSize(28);
        tv.setGravity(Gravity.CENTER);
        setContentView(tv);
    }
}
